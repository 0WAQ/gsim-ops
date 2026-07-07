"""state 存储层单测 (PG, ops_test 库)。

覆盖 PostgresStateStore:put/get round-trip、时间戳 tz 正确性、transition、
append_check、delete、list 过滤。

2026-07-07:适配三表拆分 —— FactorRecord 不再有 author/submitted_by(身份在
factor_info),PostgresStateStore 不再有 library_id(永远单库);原
test_library_id_isolation 随隔离模型一并删除(per-test 隔离改造为 per-schema
是待办,见 full-review 第二部分 I2)。本套件曾红在 HEAD 三周无人察觉(无 CI)。
"""
import pytest

from ops.core.state import FactorRecord, FactorStatus, CheckRecord

pytestmark = pytest.mark.pg


def _rec(name="A", status=FactorStatus.SUBMITTED,
         updated_at="2026-07-05T00:00:00"):
    return FactorRecord(name=name, status=status,
                        updated_at=updated_at,
                        submitted_at="2026-07-05T00:00:00")


def test_put_get_roundtrip(state_store):
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


def test_timestamp_tz_no_8h_drift(state_store):
    """naive local ISO 写入 → 读回同一 wall-clock,不因 TIMESTAMPTZ 偏 8h。"""
    rec = _rec()
    rec.submitted_at = "2026-07-05T14:30:00"
    state_store.put(rec)
    got = state_store.get("A")
    # 读回的字符串应与写入一致 (本地 wall-clock),不偏移
    assert got.submitted_at == "2026-07-05T14:30:00"


def test_transition(state_store):
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


def test_append_check_appends(state_store):
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


def test_delete(state_store):
    state_store.put(_rec())
    assert state_store.delete("A") is True
    assert state_store.get("A") is None
    assert state_store.delete("A") is False


def test_list_filters(state_store):
    state_store.put(_rec("A", FactorStatus.ACTIVE))
    state_store.put(_rec("B", FactorStatus.SUBMITTED))
    state_store.put(_rec("C", FactorStatus.SUBMITTED))
    assert {r.name for r in state_store.list()} == {"A", "B", "C"}
    assert {r.name for r in state_store.list(status=FactorStatus.SUBMITTED)} == {"B", "C"}
