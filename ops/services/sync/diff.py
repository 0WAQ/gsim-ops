"""True sync diff engine.

Lists both ends, computes per-directory diff. Replaces the manifest-based
fingerprint approach now that alpha_dump is no longer synced and the
remaining data volume (alpha_src + alpha_pnl + alpha_feature) is small
enough to enumerate directly.
"""
import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from ops.infra.s3 import S3Client, S3_MULTIPART_CHUNKSIZE, S3_MULTIPART_THRESHOLD


# Cross-machine mtime drift tolerance (seconds). Filesystems quantize to 1s
# and S3 LastModified is upload time, so the calibration step (sync.py)
# touches local mtime to match remote after every transfer. Tolerance
# handles the residual sub-second drift.
MTIME_TOLERANCE = 2.0


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
    break ties on `differ` entries.
    """
    only_local: list[str] = field(default_factory=list)
    only_remote: list[str] = field(default_factory=list)
    differ: list[str] = field(default_factory=list)
    identical: list[str] = field(default_factory=list)
    local: dict[str, FileInfo] = field(default_factory=dict)
    remote: dict[str, FileInfo] = field(default_factory=dict)

    def is_clean(self) -> bool:
        return not (self.only_local or self.only_remote or self.differ)


def walk_local(root: Path) -> dict[str, FileInfo]:
    """Walk root, return {relpath: FileInfo}. Empty dict if root missing.

    Dotfiles are skipped to match the existing convention used by the
    rest of the codebase (the remote `.state/` prefix is hidden too).
    """
    out: dict[str, FileInfo] = {}
    if not root.exists():
        return out
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
            out[rel] = FileInfo(size=st.st_size, mtime=st.st_mtime)
    return out


def list_remote(s3: S3Client, prefix: str) -> dict[str, FileInfo]:
    """List S3 objects under prefix, return {relpath: FileInfo}.

    `prefix` is normalized to end with `/` internally so the returned
    relpaths are clean (no leading slash). `mtime` is the S3 LastModified
    epoch (== upload time, not original file mtime — the sync transport
    calibrates local mtime to this value on transfer).
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
         remote: dict[str, FileInfo],
         *, deep_root: Path | None = None) -> DirDiff:
    """Compare two inventories.

    Default (no deep_root):
      - size differs                                → `differ`
      - size matches but |local.mtime - remote.mtime| > tol → `differ`
        (symmetric: catches in-place rewrites from either side. On the
        machine that did the rewrite, local.mtime > remote.mtime; on
        any other machine, remote.mtime > local.mtime because the
        previous pull calibrated local to the old LastModified)
      - otherwise                                   → `identical`

    Deep (deep_root passed):
      For every entry that the default rule marks `identical`, recompute
      local etag with the pinned multipart chunksize and compare to the
      remote etag. Mismatch → `differ`. This catches same-size content
      drift that mtime can't see.
    """
    out = DirDiff(local=dict(local), remote=dict(remote))
    for rel, lo in local.items():
        ro = remote.get(rel)
        if ro is None:
            out.only_local.append(rel)
        elif lo.size != ro.size:
            out.differ.append(rel)
        elif abs(lo.mtime - ro.mtime) > MTIME_TOLERANCE:
            out.differ.append(rel)
        else:
            out.identical.append(rel)
    for rel in remote:
        if rel not in local:
            out.only_remote.append(rel)

    if deep_root is not None:
        promoted: list[str] = []
        still_identical: list[str] = []
        for rel in out.identical:
            ro = out.remote[rel]
            if not ro.etag:
                still_identical.append(rel)
                continue
            local_etag = compute_s3_etag(deep_root / rel)
            if local_etag != ro.etag:
                promoted.append(rel)
            else:
                still_identical.append(rel)
        out.identical = still_identical
        out.differ.extend(promoted)

    out.only_local.sort()
    out.only_remote.sort()
    out.differ.sort()
    out.identical.sort()
    return out


def newer_side(rel: str, d: DirDiff,
               tolerance: float = MTIME_TOLERANCE) -> str:
    """For a `differ` entry, return which side's mtime is newer.

    Returns 'local' / 'remote' / 'tie'. Callers use this to decide whether
    push/pull should overwrite or skip-and-warn.
    """
    lo = d.local.get(rel)
    ro = d.remote.get(rel)
    if lo is None:
        return "remote"
    if ro is None:
        return "local"
    if abs(lo.mtime - ro.mtime) <= tolerance:
        return "tie"
    return "local" if lo.mtime > ro.mtime else "remote"


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
