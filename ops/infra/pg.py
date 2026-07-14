"""共享 PG 连接池:每进程按 conninfo 去重 + 退出收尾。

**为什么存在**(两个真实故障合治):

1. **退出刷屏**:`ConnectionPool(open=True)` 与其后台 worker 线程互相引用成环,
   refcount 归不了零 → 池活到解释器关闭 → GC 触发 `__del__` → join 线程在关闭
   上下文抛 `cannot join current thread`(无害但每条命令 × 每个未关的池刷一次)。
2. **连接打爆**(生产):`default_*_store()` 原先每调一次 `ConnectionPool(...)`
   新建一个池(min_size=1 立刻占 1 条连接)。`ops check` 在 `run_one` 里**每因子**
   建 state/info/snapshot 三个池,一个 worker 处理 K 个因子就攒 3K 个池、3K 条连接,
   到进程退出才释放;20 个 fork worker 一拥而上,秒破 PG 默认 `max_connections=100`
   → `FATAL: sorry, too many clients already`,连带别的 ops 命令全连不上。

**修法**:`get_pool(conninfo)` 按 (pid, conninfo) 缓存 —— 同一进程内**同 conninfo
只有一个池**。state/info/snapshot 三表同库同 conninfo,于是塌成一个共享池;一个
check worker 的连接占用从 3K 降到 1。`ensure_schema` 保证每个 (池, DDL) 只建表一次
(不再每次 store 构造都往 DB 发一遍 idempotent DDL)。`get_pool` 建的池登记进
`_pools`,`atexit` 在退出前显式 `close()`,治故障 1。

**fork 安全**:缓存键带 pid,fork 子进程 pid 不同 → 自建自己的池;`register_at_fork`
在子进程清空缓存,丢弃继承自父进程的池对象(其 worker 线程不随 fork 存活)。
`atexit` 只关本进程建的池。

注:`default_*_store` 之外零散的直接建池点已无;DDL 已滚出 store `__init__`
(现由 ensure_schema 兜)。
"""
from __future__ import annotations

import atexit
import os
import threading
from datetime import datetime
from typing import TYPE_CHECKING, cast

from psycopg_pool import ConnectionPool

if TYPE_CHECKING:
    # LiteralString 是 3.11+ 才进 typing(项目 py 下限 3.10),从 typing_extensions
    # 取 backport。注解经 __future__ 惰性化,这些名字只在类型检查期解析,运行期
    # 不 import —— 兼容 py3.10 且零运行期依赖。
    from psycopg import Connection
    from psycopg.rows import TupleRow
    from typing_extensions import LiteralString

    _Pool = ConnectionPool[Connection[TupleRow]]  # psycopg 默认连接类型

_lock = threading.Lock()
_pools: list[tuple[int, _Pool]] = []          # (创建时 pid, pool) —— atexit close 用
_pool_cache: dict[tuple[int, str], _Pool] = {}  # (pid, conninfo) -> 复用池
_schema_done: set[tuple[int, int]] = set()    # (id(pool), hash(ddl)) —— 建表去重


def get_pool(conninfo: str, *, max_size: int = 10) -> _Pool:
    """返回本进程内 conninfo 唯一的连接池;重复调用复用同一个池,不再每次新建。

    fork 子进程按 pid 隔离,自建自己的池(父进程的池 worker 线程不随 fork 存活)。
    """
    key = (os.getpid(), conninfo)
    with _lock:
        pool = _pool_cache.get(key)
        if pool is None:
            # cast:psycopg_pool 构造推不出 CT 默认值(库 typing 毛刺),显式绑定。
            pool = cast(
                "_Pool",
                ConnectionPool(conninfo, min_size=1, max_size=max_size, open=True),
            )
            _pool_cache[key] = pool
            _pools.append((os.getpid(), pool))
        return pool


def ensure_schema(pool: _Pool, ddl: LiteralString) -> None:
    """对给定 (pool, ddl) 只执行一次建表 —— 避免每次 store 构造都往 DB 发一遍
    idempotent DDL(check 每因子建 store,原先每因子发 3 条 DDL)。"""
    key = (id(pool), hash(ddl))
    with _lock:
        if key in _schema_done:
            return
    with pool.connection() as conn:
        conn.execute(ddl)
    with _lock:
        _schema_done.add(key)


def _close_my_pools() -> None:
    """关闭本进程(pid 匹配)登记的所有池。退出阶段收尾,任何异常都吞掉。"""
    me = os.getpid()
    with _lock:
        mine = [pool for (pid, pool) in _pools if pid == me]
    for pool in mine:
        try:
            pool.close()
        except Exception:
            pass


def _reset_after_fork() -> None:
    """子进程继承父进程的缓存/登记表,但那些池的 worker 线程不随 fork 存活。
    全清 —— 子进程只登记/关闭自己新建的池,绝不触碰继承来的父进程池对象。"""
    with _lock:
        _pools.clear()
        _pool_cache.clear()
        _schema_done.clear()


atexit.register(_close_my_pools)
os.register_at_fork(after_in_child=_reset_after_fork)


# ---------------------------------------------------------------------------
# ISO string <-> TIMESTAMPTZ 边界转换(正主:原 store/snapshot 两个 pg_store
# 各自镜像同名私有函数,收敛至此)
# ---------------------------------------------------------------------------

def probe(conninfo: str, *, statements: tuple[str, ...] = (),
          timeout: int = 5) -> None:
    """诊断用有界直连探测(ops setup 等):不走 get_pool —— 进程级池注册表
    不该被探测污染,且池的重连重试会让"PG 不可达"挂起半分钟以上,诊断命令
    必须秒级失败。连接/语句失败抛原异常,由调用方转成报告。"""
    import psycopg
    with psycopg.connect(conninfo, connect_timeout=timeout) as conn:
        for stmt in statements:
            conn.execute(stmt)  # type: ignore[arg-type]  # 调用方给的是白名单字面语句


def ts_in(v: str | None) -> str | None:
    """FactorRecord/Snapshot 的 ISO string(naive local,如 2026-07-04T01:45:33)
    -> TIMESTAMPTZ 可正确落库的值。string 不带时区,是本地墙钟;打上本地 tz,
    否则 PG 按 UTC 解释偏 8h。"""
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return v  # 交给 PG 解析
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.isoformat(timespec="seconds")


def ts_out(v) -> str | None:
    """TIMESTAMPTZ(psycopg 给 tz-aware datetime)-> naive local ISO string,
    与 utils/clock.now_iso 格式一致(无 tz 后缀)。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is not None:
            v = v.astimezone().replace(tzinfo=None)
        return v.isoformat(timespec="seconds")
    return str(v)
