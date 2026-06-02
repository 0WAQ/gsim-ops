"""True sync diff engine.

Lists both ends, computes per-directory diff. Identity is by S3 etag
(boto3 multipart-aware) — mtime no longer participates in identical vs.
differ classification. The local etag is cached in `etag_cache.py` keyed
on (rel, mtime, size) so steady-state syncs don't re-hash 482GB.

mtime survives only as the cache invalidation key. Direction selection
(which side overwrites the other on conflict) has been removed — sync
never auto-overwrites when etag differs; user must manually resolve.
"""
import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from tqdm import tqdm

from ops.infra.s3 import S3Client, S3_MULTIPART_CHUNKSIZE, S3_MULTIPART_THRESHOLD
from ops.services.sync import etag_cache


@dataclass
class FileInfo:
    size: int
    mtime: float
    etag: str = ""


@dataclass
class DirDiff:
    """Per-directory diff result.

    Buckets are disjoint: every relpath that appears in either side lands in
    exactly one of `only_local` / `only_remote` / `differ` / `identical`.
    `local` / `remote` carry full metadata for callers that need mtime to
    break ties on `differ` entries via `newer_side`.
    """
    only_local: list[str] = field(default_factory=list)
    only_remote: list[str] = field(default_factory=list)
    differ: list[str] = field(default_factory=list)
    identical: list[str] = field(default_factory=list)
    local: dict[str, FileInfo] = field(default_factory=dict)
    remote: dict[str, FileInfo] = field(default_factory=dict)

    def is_clean(self) -> bool:
        return not (self.only_local or self.only_remote or self.differ)


def walk_local(root: Path, *, subdir: str, cache: dict,
               recompute: bool = False,
               progress_desc: str | None = None) -> dict[str, FileInfo]:
    """Walk root, returning {relpath: FileInfo} with etag populated.

    For each file: stat, then either hit the etag cache on (mtime, size)
    or recompute via `compute_s3_etag` and update the cache in-place. The
    caller is responsible for `etag_cache.save(...)` afterwards.

    `recompute=True` ignores the cache for this walk (still updates it
    with fresh values). Used by `--deep`.

    Dotfiles are skipped to match the rest of the codebase's convention
    (the remote `.state/` prefix is hidden too).
    """
    out: dict[str, FileInfo] = {}
    if not root.exists():
        return out

    # Two-pass: first stat everything to know the total, then hash with
    # progress feedback only for cache misses (so a no-op sync stays silent).
    candidates: list[tuple[str, Path, float, int]] = []
    for dirpath, _, filenames in os.walk(root):
        for fname in filenames:
            if fname.startswith("."):
                continue
            full = Path(dirpath) / fname
            try:
                st = full.stat()
            except OSError:
                continue
            rel = str(full.relative_to(root))
            candidates.append((rel, full, st.st_mtime, st.st_size))

    to_hash: list[tuple[str, Path, float, int]] = []
    for rel, full, mtime, size in candidates:
        cached = (None if recompute
                  else etag_cache.lookup(cache, subdir, rel, mtime, size))
        if cached is not None:
            out[rel] = FileInfo(size=size, mtime=mtime, etag=cached)
        else:
            to_hash.append((rel, full, mtime, size))

    if to_hash:
        desc = progress_desc or f"hash {subdir}"
        for rel, full, mtime, size in tqdm(to_hash, desc=f"  {desc}",
                                           unit="file"):
            try:
                etag = compute_s3_etag(full)
            except OSError:
                continue
            etag_cache.put(cache, subdir, rel, mtime, size, etag)
            out[rel] = FileInfo(size=size, mtime=mtime, etag=etag)

    return out


def list_remote(s3: S3Client, prefix: str) -> dict[str, FileInfo]:
    """List S3 objects under prefix, return {relpath: FileInfo}.

    `prefix` is normalized to end with `/` internally so the returned
    relpaths are clean (no leading slash). `mtime` is the S3 LastModified
    epoch (== upload time, not original file mtime — the sync transport
    calibrates local mtime to this value on transfer, which both keeps
    the etag cache warm and gives `newer_side` a usable direction signal
    for files that haven't been edited since their last transfer).
    """
    pfx = prefix.rstrip("/") + "/"
    out: dict[str, FileInfo] = {}
    for obj in s3.list_objects(pfx):
        rel = obj["key"][len(pfx):]
        if not rel:
            continue
        out[rel] = FileInfo(size=obj["size"], mtime=obj["mtime"],
                            etag=obj["etag"])
    return out


def diff(local: dict[str, FileInfo],
         remote: dict[str, FileInfo]) -> DirDiff:
    """Compare two inventories. etag is the authority.

    - rel only on one side          → `only_local` / `only_remote`
    - both sides + etags match      → `identical`
    - both sides + etags differ     → `differ`

    Note: an empty local or remote etag (e.g. listing without etag, or a
    walk that failed to hash) is treated as "unknown" and falls to
    `differ` to be safe — better an extra transfer than a missed change.
    """
    out = DirDiff(local=dict(local), remote=dict(remote))
    for rel, lo in local.items():
        ro = remote.get(rel)
        if ro is None:
            out.only_local.append(rel)
            continue
        if lo.etag and ro.etag and lo.etag == ro.etag:
            out.identical.append(rel)
        else:
            out.differ.append(rel)
    for rel in remote:
        if rel not in local:
            out.only_remote.append(rel)

    out.only_local.sort()
    out.only_remote.sort()
    out.differ.sort()
    out.identical.sort()
    return out


def compute_s3_etag(path: Path,
                    chunksize: int = S3_MULTIPART_CHUNKSIZE,
                    threshold: int = S3_MULTIPART_THRESHOLD) -> str:
    """Compute the etag boto3 would produce for this file.

    Single-part (size < threshold): plain MD5 hex.
    Multipart: md5(concat(md5(part) for part in parts)).hex() + '-N'

    Must use the same threshold/chunksize as upload time, else the
    multipart etag won't match. Both come from infra/s3.py constants
    that the S3Client TransferConfig also uses, so they stay in sync.
    """
    size = path.stat().st_size
    if size < threshold:
        h = hashlib.md5()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    part_md5s: list[bytes] = []
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunksize)
            if not chunk:
                break
            part_md5s.append(hashlib.md5(chunk).digest())
    composite = hashlib.md5(b"".join(part_md5s)).hexdigest()
    return f"{composite}-{len(part_md5s)}"
