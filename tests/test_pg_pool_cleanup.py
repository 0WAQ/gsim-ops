"""ops/infra/pg.py 池注册表的行为测试(无需真 PG,用假池)。

覆盖 D2 两半:
- get_pool 按 (pid, conninfo) 去重(治连接打爆);
- ensure_schema 每 (pool, ddl) 只建表一次;
- atexit 只关本进程建的池、fork 子进程重置缓存(治退出刷屏 + fork 隔离)。
"""
import os

import ops.infra.pg as pg


class _FakePool:
    """假连接池:记录 close;connection() 返回一个记录 execute 的假连接。"""

    def __init__(self, conninfo="", **kw):
        self.conninfo = conninfo
        self.closed = False
        self.executed: list[str] = []

    def close(self):
        self.closed = True

    def connection(self):
        pool = self

        class _Ctx:
            def __enter__(self_):
                return self_

            def __exit__(self_, *a):
                return False

            def execute(self_, sql):
                pool.executed.append(sql)

        return _Ctx()


def _reset_registry():
    with pg._lock:
        pg._pools.clear()
        pg._pool_cache.clear()
        pg._schema_done.clear()


# ---- get_pool 去重 ----

def test_get_pool_dedups_same_conninfo(monkeypatch):
    _reset_registry()
    monkeypatch.setattr(pg, "ConnectionPool", _FakePool)

    p1 = pg.get_pool("host=x dbname=ops")
    p2 = pg.get_pool("host=x dbname=ops")
    assert p1 is p2  # 同 conninfo 复用同一个池 —— 连接不再每次新建

    p3 = pg.get_pool("host=y dbname=ops")
    assert p3 is not p1  # 不同 conninfo 各自建池


def test_get_pool_registers_for_atexit_close(monkeypatch):
    _reset_registry()
    monkeypatch.setattr(pg, "ConnectionPool", _FakePool)

    pool = pg.get_pool("host=x dbname=ops")
    pg._close_my_pools()
    assert pool.closed


# ---- ensure_schema 只跑一次 ----

def test_ensure_schema_runs_once_per_pool_ddl(monkeypatch):
    _reset_registry()
    monkeypatch.setattr(pg, "ConnectionPool", _FakePool)
    pool = pg.get_pool("host=x dbname=ops")

    pg.ensure_schema(pool, "CREATE TABLE a")
    pg.ensure_schema(pool, "CREATE TABLE a")  # 第二次应短路,不再发 DDL
    pg.ensure_schema(pool, "CREATE TABLE b")  # 不同 DDL 各跑一次

    assert pool.executed == ["CREATE TABLE a", "CREATE TABLE b"]


# ---- 退出收尾 + fork 隔离 ----

def test_close_skips_other_pid():
    _reset_registry()
    mine, theirs = _FakePool(), _FakePool()
    with pg._lock:
        pg._pools.append((os.getpid(), mine))
        pg._pools.append((os.getpid() + 1, theirs))

    pg._close_my_pools()

    assert mine.closed and not theirs.closed


def test_close_swallows_pool_errors():
    _reset_registry()

    class _Boom(_FakePool):
        def close(self):
            raise RuntimeError("boom")

    good = _FakePool()
    with pg._lock:
        pg._pools.append((os.getpid(), _Boom()))
        pg._pools.append((os.getpid(), good))

    pg._close_my_pools()  # 不抛
    assert good.closed


def test_reset_after_fork_clears_all_registries(monkeypatch):
    _reset_registry()
    monkeypatch.setattr(pg, "ConnectionPool", _FakePool)
    pool = pg.get_pool("host=x dbname=ops")
    pg.ensure_schema(pool, "CREATE TABLE a")
    assert pg._pools and pg._pool_cache and pg._schema_done

    pg._reset_after_fork()

    assert pg._pools == [] and pg._pool_cache == {} and pg._schema_done == set()
