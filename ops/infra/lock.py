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
  **conninfo 缺失是硬错误**:静默降级成单机 fcntl 会让跨机互斥无声消失,
  比报错危险得多(JOURNAL F4)。
- **json** (单机 dev/test): per-machine `fcntl` file lock under
  `~/.cache/ops/locks/{name}.lock`。单机语义下正确;它不是生产回退。

Acquisition is **non-blocking** on both backends: if another holder has the
lock, the caller gets `FactorLocked` immediately (log a warning and skip,
don't queue).

**锁键**: `(hashtext('ops:factor_lock'), hashtext(name))` —— classid 是固定
命名空间常量。别用 `hashtext(config.library_id)` 作 classid:library_id 随
config 文件不同,两个进程会锁不是同一把锁,跨机互斥在混用 config 时失效
(JOURNAL F5)。单库世界里锁键不该有 library 维度。
"""
import fcntl
from contextlib import contextmanager
from pathlib import Path

from ops.utils.log import logger

LOCK_DIR = Path.home() / ".cache" / "ops" / "locks"

# advisory lock 的固定 classid 命名空间(server 端 hashtext('ops:factor_lock'))。
# 所有 ops 进程共享同一命名空间 —— 锁键只由因子名决定。
# **生产不可注入**(锁键随 config 漂移 = 跨机互斥无声失效)。唯一的合法覆盖方
# 是测试:config.lock_namespace(state.lock_namespace)注入本 pytest session
# 的 PG schema 名 —— advisory lock 是库级作用域,per-session schema 隔离挡不住
# 它,并行测试进程必须各锁各的命名空间。
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
def _pg_advisory_lock(name: str, conninfo: str, namespace: str = _LOCK_NAMESPACE):
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
        row = conn.execute(
            "SELECT pg_try_advisory_lock(hashtext(%s), hashtext(%s))",
            (namespace, name),
        ).fetchone()
        if not (row and row[0]):
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
                    (namespace, name),
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
        # 命名空间缺省固定;config.lock_namespace 是仅测试的注入口(见常量注释)
        namespace = getattr(config, "lock_namespace", None) or _LOCK_NAMESPACE
        with _pg_advisory_lock(name, conninfo, namespace):
            yield
    elif backend == "json":
        with _fcntl_lock(name):
            yield
    else:
        raise RuntimeError(f"unknown state_backend for factor_lock: {backend!r}")
