"""Per-factor advisory lock.

A factor flowing through submit → check → archive touches multiple resources
(staging dir, alpha_src dir, state.json, meta.json). The state-store
fcntl lock only protects state.json itself; nothing prevents two `ops check`
processes from picking up the same factor from staging concurrently and
racing to move it.

This module provides a per-factor file lock to serialize all ops mutations
on a single factor across processes. Locks live under
`~/.cache/ops/locks/{factor_name}.lock` — centralized so they don't move
with the factor directory.

Acquisition is non-blocking: if another process holds the lock, the caller
gets `FactorLocked` immediately, log a warning and skip rather than queueing.
"""
import fcntl
from contextlib import contextmanager
from pathlib import Path


LOCK_DIR = Path.home() / ".cache" / "ops" / "locks"


class FactorLocked(RuntimeError):
    """Raised when another process holds the per-factor lock."""


@contextmanager
def factor_lock(name: str):
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    path = LOCK_DIR / f"{name}.lock"
    f = path.open("a+")
    try:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise FactorLocked(name)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    finally:
        f.close()
