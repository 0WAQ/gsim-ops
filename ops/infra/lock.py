"""Per-factor advisory lock — serializes all ops mutations on a single factor.

A factor flowing through submit → check → archive touches multiple resources
(staging dir, alpha_src dir, state, meta.json). This lock serializes concurrent
`ops` operations on the same factor so two processes can't race, e.g. two
`ops check` picking up the same factor from staging and both moving it.

**Backends** (chosen by `config.state_backend`):

- **postgres**: PostgreSQL *session-level* advisory lock (`pg_try_advisory_lock`)
  on a dedicated connection held for the whole critical section. Cross-machine:
  state lives in shared PG and staging on shared JFS, so 160/150/144 can all run
  `ops check` on the same staging — a per-machine file lock cannot stop that, but
  a PG advisory lock can (PG is the one strongly-consistent store all three see).
  Session-level means the lock is released automatically when the connection
  drops (process death / SIGKILL / power loss), so there is no stuck-lock residue.

- **json / redis / no conninfo**: falls back to a per-machine `fcntl` file lock
  under `~/.cache/ops/locks/{name}.lock`. Correct for single-machine json; redis
  state is being retired and was fcntl anyway.

Acquisition is **non-blocking** on both backends: if another holder has the lock,
the caller gets `FactorLocked` immediately (log a warning and skip, don't queue).
"""
import fcntl
from contextlib import contextmanager
from pathlib import Path

from ops.utils.log import logger


LOCK_DIR = Path.home() / ".cache" / "ops" / "locks"


class FactorLocked(RuntimeError):
    """Raised when another holder (process/connection) holds the per-factor lock."""


@contextmanager
def _fcntl_lock(name: str):
    """Per-machine fcntl file lock (json/redis fallback)."""
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


@contextmanager
def _pg_advisory_lock(name: str, conninfo: str, library_id: str):
    """Cross-machine PG session-level advisory lock.

    Uses a dedicated connection (NOT the state pool) held for the whole critical
    section: session advisory locks must acquire and release on the same
    connection, and a pooled connection would be handed to the next user while
    still holding the lock. The two-int form keys the lock by (library, name) so
    different libraries never collide; hashtext runs server-side.
    """
    import psycopg

    conn = psycopg.connect(conninfo, autocommit=True)
    try:
        got = conn.execute(
            "SELECT pg_try_advisory_lock(hashtext(%s), hashtext(%s))",
            (library_id, name),
        ).fetchone()[0]
        if not got:
            raise FactorLocked(name)
        try:
            yield
        finally:
            # Best-effort explicit unlock. Closing the connection (below) also
            # releases any session advisory lock, so if the unlock statement
            # itself fails (PG blip mid-critical-section) we must NOT let that
            # exception mask a successful critical section — the lock is freed
            # by conn.close() either way. Swallow + warn.
            try:
                conn.execute(
                    "SELECT pg_advisory_unlock(hashtext(%s), hashtext(%s))",
                    (library_id, name),
                )
            except Exception as e:
                logger.warning("advisory unlock failed for ({}, {}): {}",
                               library_id, name, e)
    finally:
        conn.close()


@contextmanager
def factor_lock(name: str, config):
    """Serialize ops mutations on one factor. Non-blocking: raises FactorLocked
    if contended. Uses a cross-machine PG advisory lock on the postgres backend,
    else a per-machine fcntl file lock. `config` selects the backend."""
    backend = (getattr(config, "state_backend", None) or "json").lower()
    conninfo = getattr(config, "state_postgres_conninfo", None)
    if backend == "postgres" and conninfo:
        with _pg_advisory_lock(name, conninfo, config.library_id):
            yield
    else:
        with _fcntl_lock(name):
            yield
