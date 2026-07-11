"""state 存储层单测 (PG, ops_test 库,per-session schema 隔离)。

覆盖 PostgresStateStore:put/get round-trip、时间戳 tz 正确性、transition、
append_check、delete、list 过滤。

2026-07-07:适配三表拆分 —— FactorRecord 不再有 author/submitted_by(身份在
factor_info),PostgresStateStore 不再有 library_id(永远单库);原
test_library_id_isolation 随隔离模型一并删除。本套件曾红在 HEAD 三周无人察觉
(无 CI)。

2026-07-11(I2):隔离模型重建为 per-session schema,本组自三表拆分以来的
整组 skip 解除。**factor_state.name 有 FK → factor_info**:生产里 register
是 info+state 原子双表写,不存在无父行的 state —— 测试镜像该前置,put 前
用 seed_info 显式种父行(FK 违约不是本组要测的语义,是建库约束)。
"""
import pytest

from ops.core.state import CheckRecord, FactorRecord, FactorStatus

pytestmark = pytest.mark.pg


def _rec(name="A", status=FactorStatus.SUBMITTED,
         updated_at="2026-07-05T00:00:00"):
    return FactorRecord(name=name, status=status,
                        updated_at=updated_at,
                        submitted_at="2026-07-05T00:00:00")


def test_put_get_roundtrip(state_store, seed_info):
    seed_info("A")
    rec = _rec()
    rec.check_history = [
        CheckRecord(started_at="2026-07-05T00:00:00", finished_at="2026-07-05T00:05:00",
                    passed=True),
    ]
    state_store.put(rec)
    got = state_store.get("A")
    assert got is not None
    assert got.name == "A"
    assert got.status == FactorStatus.SUBMITTED
    assert len(got.check_history) == 1
    assert got.check_history[0].passed is True
    assert state_store.get("missing") is None


def test_timestamp_tz_no_8h_drift(state_store, seed_info):
    """naive local ISO 写入 → 读回同一 wall-clock,不因 TIMESTAMPTZ 偏 8h。"""
    seed_info("A")
    rec = _rec()
    rec.submitted_at = "2026-07-05T14:30:00"
    state_store.put(rec)
    got = state_store.get("A")
    # 读回的字符串应与写入一致 (本地 wall-clock),不偏移
    assert got.submitted_at == "2026-07-05T14:30:00"


def test_transition(state_store, seed_info):
    seed_info("A")
    state_store.put(_rec())
    state_store.transition("A", FactorStatus.CHECKING)
    assert state_store.get("A").status == FactorStatus.CHECKING
    r = state_store.transition("A", FactorStatus.ACTIVE, entered_at="2026-07-05T01:00:00")
    assert r.status == FactorStatus.ACTIVE
    got = state_store.get("A")
    assert got.status == FactorStatus.ACTIVE
    assert got.entered_at == "2026-07-05T01:00:00"


def test_transition_missing_raises(state_store):
    with pytest.raises(KeyError):
        state_store.transition("nope", FactorStatus.ACTIVE)


def test_append_check_appends(state_store, seed_info):
    seed_info("A")
    state_store.put(_rec())
    state_store.append_check("A", CheckRecord(started_at="2026-07-05T00:00:00", passed=True))
    state_store.append_check("A", CheckRecord(started_at="2026-07-05T01:00:00", passed=False,
                                              failed_stage="checkbias"))
    got = state_store.get("A")
    assert len(got.check_history) == 2
    assert got.check_history[0].passed is True
    assert got.check_history[1].failed_stage == "checkbias"


def test_append_check_missing_raises(state_store):
    with pytest.raises(KeyError):
        state_store.append_check("nope", CheckRecord(started_at="2026-07-05T00:00:00"))


def test_delete(state_store, seed_info):
    seed_info("A")
    state_store.put(_rec())
    assert state_store.delete("A") is True
    assert state_store.get("A") is None
    assert state_store.delete("A") is False


def test_list_filters(state_store, seed_info):
    seed_info("A", "B", "C")
    state_store.put(_rec("A", FactorStatus.ACTIVE))
    state_store.put(_rec("B", FactorStatus.SUBMITTED))
    state_store.put(_rec("C", FactorStatus.SUBMITTED))
    assert {r.name for r in state_store.list()} == {"A", "B", "C"}
    assert {r.name for r in state_store.list(status=FactorStatus.SUBMITTED)} == {"B", "C"}
