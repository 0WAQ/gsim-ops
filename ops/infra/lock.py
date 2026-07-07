"""Per-factor advisory lock — serializes all ops mutations on a single factor.

A factor flowing through submit → check → archive touches multiple resources
(staging dir, alpha_src dir, state, meta.json). This lock serializes concurrent
`ops` operations on the same factor so two processes can't race, e.g. two
`ops check` picking up the same factor from staging and both moving it.

**Backends** (chosen by `config.state_backend`):

- **postgres** (生产): PostgreSQL *session-level* advisory lock
  (`pg_try_advisory_lock`) on a dedicated connection held for the whole
  critical section. Cross-machine: state lives in shared PG and staging on
  shared JFS, so 160/150/144 can all run `ops check` on the same staging —
  a per-machine file lock cannot stop that, but a PG advisory lock can (PG is
  the one strongly-consistent store all three see). Session-level means the
  lock is released automatically when the connection drops (process death /
  SIGKILL / power loss), so there is no stuck-lock residue.
  **conninfo 缺失是硬错误**:2026-07-07 (Wave 1, JOURNAL F4) 前这里会静默降级
  成单机 fcntl —— 跨机互斥无声消失,比报错危险得多。
- **json** (单机 dev/test): per-machine `fcntl` file lock under
  `~/.cache/ops/locks/{name}.lock`。单机语义下正确;它不是生产回退。

Acquisition is **non-blocking** on both backends: if another holder has the
lock, the caller gets `FactorLocked` immediately (log a warning and skip,
don't queue).

**锁键 (2026-07-07 修, JOURNAL F5)**: `(hashtext('ops:factor_lock'),
hashtext(name))` —— classid 是固定命名空间常量。原实现用
`hashtext(config.library_id)` 作 classid,而 library_id 曾随 config 文件不同
(alphalib vs alphalib-juicefs):两个进程锁的不是同一把锁,跨机互斥在混用
config 的窗口期失效 (full-review S18)。单库世界里锁键不该有 library 维度。
升级注意:新旧键不同,滚动升级期间新旧版本 ops 互不互斥 —— 部署时确保无
in-flight check。
"""
import fcntl
from contextlib import contextmanager
from pathlib import Path

from ops.utils.log import logger


LOCK_DIR = Path.home() / ".cache" / "ops" / "locks"

# advisory lock 的固定 classid 命名空间(server 端 hashtext('ops:factor_lock'))。
# 所有 ops 进程共享同一命名空间 —— 锁键只由因子名决定。
_LOCK_NAMESPACE = "ops:factor_lock"


class FactorLocked(RuntimeError):
    """Raised when another holder (process/connection) holds the per-factor lock."""


@contextmanager
def _fcntl_lock(name: str):
    """Per-machine fcntl file lock (json dev/test backend)."""
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
def _pg_advisory_lock(name: str, conninfo: str):
    """Cross-machine PG session-level advisory lock.

    Uses a dedicated connection (NOT the state pool) held for the whole critical
    section: session advisory locks must acquire and release on the same
    connection, and a pooled connection would be handed to the next user while
    still holding the lock. The two-int form keys the lock by
    (namespace, name); hashtext runs server-side.
    """
    import psycopg

    conn = psycopg.connect(conninfo, autocommit=True)
    try:
        got = conn.execute(
            "SELECT pg_try_advisory_lock(hashtext(%s), hashtext(%s))",
            (_LOCK_NAMESPACE, name),
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
                    (_LOCK_NAMESPACE, name),
                )
            except Exception as e:
                logger.warning("advisory unlock failed for {}: {}", name, e)
    finally:
        conn.close()


@contextmanager
def factor_lock(name: str, config):
    """Serialize ops mutations on one factor. Non-blocking: raises FactorLocked
    if contended. postgres 后端 = 跨机 PG advisory lock(conninfo 缺失硬错误,
    不再静默降级);json 后端 = 单机 fcntl。`config` selects the backend."""
    backend = (getattr(config, "state_backend", None) or "json").lower()
    if backend == "postgres":
        conninfo = getattr(config, "state_postgres_conninfo", None)
        if not conninfo:
            # 静默退回 fcntl = 跨机互斥无声消失。宁可停下来。
            raise RuntimeError(
                "state_backend=postgres 但 state.postgres conninfo 不可用 —— "
                "跨机 factor_lock 无法建立,拒绝静默降级为单机锁 "
                "(检查 config.state.postgres.* 与密码文件)"
            )
        with _pg_advisory_lock(name, conninfo):
            yield
    elif backend == "json":
        with _fcntl_lock(name):
            yield
    else:
        raise RuntimeError(f"unknown state_backend for factor_lock: {backend!r}")
