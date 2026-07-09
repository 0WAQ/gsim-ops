"""ops/infra/pg.py 池收尾登记表的行为测试(无需 PG,用假池)。

验证 D2 前身:atexit 只关本进程建的池、fork 子进程重置登记表。
"""
import os

import ops.infra.pg as pg


class _FakePool:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _reset_registry():
    with pg._lock:
        pg._pools.clear()


def test_close_my_pools_closes_tracked(monkeypatch):
    _reset_registry()
    p1, p2 = _FakePool(), _FakePool()
    pg.track_pool(p1)
    pg.track_pool(p2)

    pg._close_my_pools()

    assert p1.closed and p2.closed


def test_close_skips_other_pid(monkeypatch):
    """别的 pid 登记的池不该被本进程关闭(fork 隔离的核心)。"""
    _reset_registry()
    mine, theirs = _FakePool(), _FakePool()
    pg.track_pool(mine)
    # 手工插一个"另一个进程建的"登记项
    with pg._lock:
        pg._pools.append((os.getpid() + 1, theirs))

    pg._close_my_pools()

    assert mine.closed
    assert not theirs.closed


def test_close_swallows_pool_errors():
    """退出阶段收尾:某个池 close() 抛错不能中断其它池的关闭。"""
    _reset_registry()

    class _Boom:
        def close(self):
            raise RuntimeError("boom")

    good = _FakePool()
    pg.track_pool(_Boom())
    pg.track_pool(good)

    pg._close_my_pools()  # 不抛

    assert good.closed


def test_reset_after_fork_clears_registry():
    _reset_registry()
    pg.track_pool(_FakePool())
    assert pg._pools

    pg._reset_after_fork()

    assert pg._pools == []
