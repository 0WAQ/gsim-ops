"""共享 PG 连接池的进程退出收尾。

`psycopg_pool.ConnectionPool(open=True)` 会起后台 worker 线程维护最小连接数;
pool 与 worker 线程互相引用成环,refcount 永远归不了零,于是池一直活到**解释器
关闭**。此时 GC 触发 `ConnectionPool.__del__` → `gather(workers)` → join 线程,
而在解释器关闭上下文里 join 抛 `RuntimeError: cannot join current thread` —— 无害
(不影响命令结果),但每条 ops 命令、每个未关的池刷一次 traceback(state + info +
snapshot 三池 → 一条命令刷三次)。

修法:在此登记每个建好的池,注册 `atexit` 钩子在解释器关闭**前**显式 `close()`
(close 会有序停 worker;此后 `__del__` 见池已关即空转,不再刷 traceback)。

fork 安全:登记项带创建时 pid,`atexit` 只关本进程建的池;`register_at_fork` 在
子进程里清空登记表 —— `ops check` 的 ProcessPoolExecutor worker 各自新建 store,
其池登记在子进程名下、由子进程退出时关闭,不会去动继承自父进程的池对象。

注:这是 full-review D2「每进程一个池注册表」的**最小前身** —— 只做生命周期收尾,
尚未按 conninfo 去重(三表同库本可共享一个池)。去重那步见
`docs/factor-aggregate-plan.md` 阶段 1。
"""
import atexit
import os
import threading

_lock = threading.Lock()
_pools: list[tuple[int, object]] = []  # (创建时 pid, pool)


def track_pool(pool: object) -> None:
    """登记一个刚建好的连接池,交由 atexit 在进程退出前 close()。"""
    with _lock:
        _pools.append((os.getpid(), pool))


def _close_my_pools() -> None:
    """关闭本进程(pid 匹配)登记的所有池。退出阶段收尾,任何异常都吞掉。"""
    me = os.getpid()
    with _lock:
        mine = [pool for (pid, pool) in _pools if pid == me]
    for pool in mine:
        try:
            pool.close()  # type: ignore[attr-defined]
        except Exception:
            pass


def _reset_after_fork() -> None:
    """子进程继承父进程的登记表,但那些池的 worker 线程不随 fork 存活。清空后
    子进程只登记/关闭自己新建的池,绝不触碰继承来的父进程池对象。"""
    with _lock:
        _pools.clear()


atexit.register(_close_my_pools)
os.register_at_fork(after_in_child=_reset_after_fork)
