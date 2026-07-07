"""Cache layout helper.

All ops state/cache files live under ~/.cache/ops/. Historically each
config-dependent file was prefixed with md5(config_path)[:8], which made
files unsyncable across machines (paths differ → hashes differ).

We now key by `library_id` — a stable name derived from the factor library
itself (default: `alpha_src.parent.name`). Files live at
~/.cache/ops/lib/<library_id>/<filename>. `cache_path()` resolves the new
path and one-shot migrates a legacy file on first call.

Locks stay at ~/.cache/ops/locks/ — fcntl, per-machine, never synced.
"""
from pathlib import Path

CACHE_ROOT = Path.home() / ".cache" / "ops"


def cache_path(library_id: str, filename: str, *, legacy_hash: str | None = None) -> Path:
    """Resolve ~/.cache/ops/lib/<library_id>/<filename>.

    If the new path doesn't exist, look for a legacy file and rename it
    into place:
    - `legacy_hash` given: legacy at ~/.cache/ops/<legacy_hash>.<filename>
    - `legacy_hash` None: legacy at ~/.cache/ops/<filename> (stable-name files)
    """
    new_dir = CACHE_ROOT / "lib" / library_id
    new_dir.mkdir(parents=True, exist_ok=True)
    new_path = new_dir / filename
    if not new_path.exists():
        if legacy_hash is not None:
            legacy = CACHE_ROOT / f"{legacy_hash}.{filename}"
        else:
            legacy = CACHE_ROOT / filename
        if legacy.exists() and legacy.is_file():
            legacy.rename(new_path)
    return new_path


def library_cache_dir(library_id: str) -> Path:
    """Return ~/.cache/ops/lib/<library_id>/, ensuring it exists."""
    d = CACHE_ROOT / "lib" / library_id
    d.mkdir(parents=True, exist_ok=True)
    return d
