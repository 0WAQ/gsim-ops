"""批量命令骨架 (_batch.py) + transition CAS 的无 PG 单测。

json 后端 + fcntl 锁即可驱动,不需要 ops_test PG —— 这是骨架抽取的红利之一:
锁循环 / 锁内复验 / CAS 语义第一次有了可断言的行为测试(此前四份手抄循环
只能靠 PG 组集成测试覆盖,而 PG 组自三表拆分后一直 skip)。
"""
from types import SimpleNamespace

import pytest

import ops.infra.lock as lock_mod
from ops.core.state import FactorRecord, FactorStatus
from ops.infra.store import StateConflict
from ops.infra.store.json_store import JsonStateStore
from ops.services._batch import BatchResult, SkipFactor, apply_locked, confirm_or_abort


@pytest.fixture
def jconfig(tmp_path, monkeypatch):
    """最小 config:json 后端 → factor_lock 走 fcntl;锁目录隔离到 tmp。"""
    monkeypatch.setattr(lock_mod, "LOCK_DIR", tmp_path / "locks")
    return SimpleNamespace(state_backend="json")


@pytest.fixture
def jstore(tmp_path):
    return JsonStateStore(tmp_path / "factor_state.json")


def _rec(name, status=FactorStatus.SUBMITTED):
    return FactorRecord(name=name, status=status,
                        updated_at="2026-07-05T00:00:00",
                        submitted_at="2026-07-05T00:00:00")


# ---------------------------------------------------------------------------
# transition(expect=) CAS
# ---------------------------------------------------------------------------

def test_transition_cas_pass(jstore):
    jstore.put(_rec("A", FactorStatus.REJECTED))
    rec = jstore.transition("A", FactorStatus.ACTIVE, expect=FactorStatus.REJECTED)
    assert rec.status == FactorStatus.ACTIVE


def test_transition_cas_conflict(jstore):
    jstore.put(_rec("A", FactorStatus.ACTIVE))
    with pytest.raises(StateConflict):
        jstore.transition("A", FactorStatus.ACTIVE, expect=FactorStatus.REJECTED)
    # 冲突时不写:状态保持原样
    assert jstore.get("A").status == FactorStatus.ACTIVE


def test_transition_no_expect_unguarded(jstore):
    # expect 缺省时保持旧行为(无守卫)—— 调用方按需选择
    jstore.put(_rec("A", FactorStatus.ACTIVE))
    jstore.transition("A", FactorStatus.SUBMITTED)
    assert jstore.get("A").status == FactorStatus.SUBMITTED


# ---------------------------------------------------------------------------
# apply_locked 路由:done / SkipFactor / StateConflict / 异常
# ---------------------------------------------------------------------------

def test_apply_locked_routes_outcomes(jconfig, jstore):
    jstore.put(_rec("AlphaOk", FactorStatus.REJECTED))
    jstore.put(_rec("AlphaGone", FactorStatus.ACTIVE))  # 复验将不通过
    jstore.put(_rec("AlphaCas", FactorStatus.ACTIVE))   # CAS 将冲突

    def action(name: str) -> None:
        fresh = jstore.get(name)
        if fresh.status != FactorStatus.REJECTED and name == "AlphaGone":
            raise SkipFactor(f"状态已变: {fresh.status.value}")
        if name == "AlphaCas":
            jstore.transition(name, FactorStatus.ACTIVE, expect=FactorStatus.REJECTED)
            return
        if name == "AlphaBoom":
            raise RuntimeError("boom")
        jstore.transition(name, FactorStatus.ACTIVE, expect=FactorStatus.REJECTED)

    res = apply_locked(["AlphaOk", "AlphaGone", "AlphaCas", "AlphaBoom"],
                       jconfig, action, verb="test")
    assert res.done == ["AlphaOk"]
    assert [n for n, _ in res.skipped] == ["AlphaGone", "AlphaCas"]
    assert [n for n, _ in res.failed] == ["AlphaBoom"]
    assert res.locked == []
    # 副作用只发生在 done 上
    assert jstore.get("AlphaOk").status == FactorStatus.ACTIVE
    assert jstore.get("AlphaCas").status == FactorStatus.ACTIVE  # 未被改写


def test_apply_locked_failure_does_not_abort_batch(jconfig, jstore):
    jstore.put(_rec("AlphaA", FactorStatus.REJECTED))
    jstore.put(_rec("AlphaB", FactorStatus.REJECTED))
    calls = []

    def action(name: str) -> None:
        calls.append(name)
        if name == "AlphaA":
            raise RuntimeError("boom")

    res = apply_locked(["AlphaA", "AlphaB"], jconfig, action, verb="test")
    assert calls == ["AlphaA", "AlphaB"]  # A 炸不阻断 B
    assert res.done == ["AlphaB"]
    assert [n for n, _ in res.failed] == ["AlphaA"]


# ---------------------------------------------------------------------------
# confirm_or_abort
# ---------------------------------------------------------------------------

def test_confirm_yes_flag_skips_prompt(jconfig):
    assert confirm_or_abort("x", 3, yes=True) is True


@pytest.mark.parametrize("answer,expected", [("y", True), ("yes", True),
                                             ("", False), ("n", False)])
def test_confirm_prompt(monkeypatch, answer, expected):
    monkeypatch.setattr("builtins.input", lambda _: answer)
    assert confirm_or_abort("x", 1, yes=False) is expected


def test_batch_result_defaults_independent():
    a, b = BatchResult(), BatchResult()
    a.done.append("X")
    assert b.done == []  # field(default_factory) 不共享
