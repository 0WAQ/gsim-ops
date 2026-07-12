"""factor_history 全操作审计表(schema v2b)行为测试(PG)。

钉住的语义:
- 事件与业务写同事务发射(transition 的 op / append_check / delete 的 op);
- 置 ACTIVE 自动发射 'entered'(check 归档 / approve / backfill 三径合流,
  漏记结构上不可能);
- **历史活过 rm**(无 FK,v2b 立项动机);
- last_fail 派生 = 最新一条 passed=FALSE 的 check 事件;
- json dev/test 后端的 last_fail 从 check_history 合成(语义一致)。
"""
import pytest

from ops.core.state import CheckRecord, FactorRecord, FactorStatus

pytestmark = pytest.mark.pg


def _rec(name, status=FactorStatus.SUBMITTED):
    entered = "2026-07-01T00:00:00" if status == FactorStatus.ACTIVE else None
    return FactorRecord(name=name, status=status,
                        updated_at="2026-07-05T00:00:00", entered_at=entered)


def test_transition_emits_op_and_auto_entered(state_store, seed_info):
    seed_info("A")
    state_store.put(_rec("A", FactorStatus.REJECTED))
    state_store.transition("A", FactorStatus.ACTIVE, op="approve",
                           actor="wbai", entered_at="2026-07-05T02:00:00")
    ops = [(e.op, e.actor) for e in state_store.history("A")]
    assert ("approve", "wbai") in ops
    assert ("entered", "wbai") in ops


def test_transition_without_op_no_command_event(state_store, seed_info):
    seed_info("A")
    state_store.put(_rec("A"))
    # CHECKING 瞬时态:无事件(设计:只记完成的操作)
    state_store.transition("A", FactorStatus.CHECKING)
    assert state_store.history("A") == []


def test_history_survives_delete(state_store, seed_info):
    seed_info("A")
    state_store.put(_rec("A"))
    state_store.transition("A", FactorStatus.SUBMITTED, op="restage")
    assert state_store.delete("A", op="rm", actor="wbai") is True
    ops = [e.op for e in state_store.history("A")]
    assert ops == ["restage", "rm"]  # 事件无 FK,活过删除
    assert state_store.get("A") is None


def test_last_fail_is_latest_fail_event(state_store, seed_info):
    seed_info("A")
    state_store.put(_rec("A"))
    assert state_store.last_fail("A") is None
    state_store.append_check("A", CheckRecord(
        started_at="2026-07-05T00:00:00", finished_at="2026-07-05T00:05:00",
        passed=False, failed_stage="checkbias", fail_reason="bias"))
    state_store.append_check("A", CheckRecord(
        started_at="2026-07-06T00:00:00", finished_at="2026-07-06T00:05:00",
        passed=False, failed_stage="correlation", fail_reason="corr"))
    state_store.append_check("A", CheckRecord(
        started_at="2026-07-07T00:00:00", finished_at="2026-07-07T00:05:00",
        passed=True))
    lf = state_store.last_fail("A")
    assert lf is not None and lf.failed_stage == "correlation"
    assert lf.at == "2026-07-06T00:05:00"


def test_register_emits_submit_event(test_config, seed_factor):
    from ops.core.factor import FactorIdentity
    from ops.infra.repository import FactorRepository
    _, config = test_config
    repo = FactorRepository(config)
    repo.register(FactorIdentity(name="AlphaWbaiNew", author="wbai"),
                  submitted_at="2026-07-05T00:00:00", op="submit")
    assert [e.op for e in repo.history("AlphaWbaiNew")] == ["submit"]


def test_register_active_emits_backfill_and_entered(test_config):
    from ops.core.factor import FactorIdentity
    from ops.infra.repository import FactorRepository
    _, config = test_config
    repo = FactorRepository(config)
    repo.register(FactorIdentity(name="AlphaWbaiLegacy", author="wbai"),
                  status=FactorStatus.ACTIVE,
                  entered_at="2026-07-05T00:00:00", op="backfill")
    ops = [e.op for e in repo.history("AlphaWbaiLegacy")]
    assert ops == ["backfill", "entered"]


def test_json_backend_last_fail_from_check_history(tmp_path):
    from ops.infra.store.json_store import JsonStateStore
    store = JsonStateStore(tmp_path / "state.json")
    store.put(_rec("A"))
    assert store.last_fail("A") is None
    store.append_check("A", CheckRecord(
        started_at="2026-07-05T00:00:00", finished_at="2026-07-05T00:05:00",
        passed=False, failed_stage="correlation", fail_reason="corr"))
    store.append_check("A", CheckRecord(
        started_at="2026-07-06T00:00:00", passed=True))
    lf = store.last_fail("A")
    assert lf is not None and lf.failed_stage == "correlation"
    evs = store.history("A")  # v2c: 合成 check 事件(生命周期 op 缺席)
    assert [e.op for e in evs] == ["check", "check"]
