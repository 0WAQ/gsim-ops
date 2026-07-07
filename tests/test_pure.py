"""纯函数 + json 回退后端单测 (无需 PG)。

覆盖:
- JsonStateStore CRUD (单机 dev/test 后端正确性;derived 层测试随层删除, Wave 2)
"""
import pytest

from ops.infra.store.json_store import JsonStateStore
from ops.core.state import FactorRecord, FactorStatus, CheckRecord


# ---------------------------------------------------------------------------
# JsonStateStore
# ---------------------------------------------------------------------------

@pytest.fixture
def jstate(tmp_path):
    return JsonStateStore(tmp_path / "factor_state.json")


def _rec(name="A", status=FactorStatus.SUBMITTED):
    # FactorRecord 2026-07-06 起是纯状态机,不含 author/submitted_by
    # (身份在 factor_info)。旧签名让本套件在三表重构后红了三周无人察觉(无 CI)。
    return FactorRecord(name=name, status=status,
                        updated_at="2026-07-05T00:00:00",
                        submitted_at="2026-07-05T00:00:00")


def test_json_state_put_get(jstate):
    jstate.put(_rec())
    got = jstate.get("A")
    assert got is not None
    assert got.name == "A"
    assert got.status == FactorStatus.SUBMITTED
    assert jstate.get("missing") is None


def test_json_state_transition(jstate):
    jstate.put(_rec())
    jstate.transition("A", FactorStatus.CHECKING)
    assert jstate.get("A").status == FactorStatus.CHECKING
    jstate.transition("A", FactorStatus.ACTIVE, entered_at="2026-07-05T01:00:00")
    got = jstate.get("A")
    assert got.status == FactorStatus.ACTIVE
    assert got.entered_at == "2026-07-05T01:00:00"


def test_json_state_append_check(jstate):
    jstate.put(_rec())
    jstate.append_check("A", CheckRecord(started_at="2026-07-05T00:00:00", passed=True))
    jstate.append_check("A", CheckRecord(started_at="2026-07-05T01:00:00", passed=False))
    got = jstate.get("A")
    assert len(got.check_history) == 2
    assert got.check_history[0].passed is True
    assert got.check_history[1].passed is False


def test_json_state_delete(jstate):
    jstate.put(_rec())
    assert jstate.delete("A") is True
    assert jstate.get("A") is None
    assert jstate.delete("A") is False


def test_json_state_list_filters(jstate):
    jstate.put(_rec("A", FactorStatus.ACTIVE))
    jstate.put(_rec("B", FactorStatus.SUBMITTED))
    jstate.put(_rec("C", FactorStatus.SUBMITTED))
    assert {r.name for r in jstate.list()} == {"A", "B", "C"}
    assert {r.name for r in jstate.list(status=FactorStatus.SUBMITTED)} == {"B", "C"}
