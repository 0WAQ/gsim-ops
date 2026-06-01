"""True sync diff engine.

Lists both ends, computes per-directory diff. Replaces the manifest-based
fingerprint approach now that alpha_dump is no longer synced and the
remaining data volume (alpha_src + alpha_pnl + alpha_feature) is small
enough to enumerate directly.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from ops.infra.s3 import S3Client


# Cross-machine mtime drift tolerance (seconds). Filesystems quantize to 1s
# and S3 LastModified is upload time, not file mtime — so a few-second gap
# between identically-sized files should not be flagged.
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
    relpaths are clean (no leading slash).
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
    """Compare two inventories by size.

    A file is `identical` iff sizes match. Content drift with identical
    size is rare in this codebase (npy + source files) and is the job of
    `verify --deep` to catch via etag/md5.
    """
    out = DirDiff(local=dict(local), remote=dict(remote))
    for rel, lo in local.items():
        ro = remote.get(rel)
        if ro is None:
            out.only_local.append(rel)
        elif lo.size == ro.size:
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
