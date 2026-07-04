"""纯函数 + json 回退后端单测 (无需 PG)。

覆盖:
- metric_get / sort_key 逐键语义 (三处真相源的 Python 侧)
- JsonStateStore / JsonDerivedStore CRUD + get_all 下推 (回退后端正确性)
"""
import pytest

from ops.infra.derived.base import DerivedRecord, metric_get, sort_key
from ops.infra.derived.json_store import JsonDerivedStore
from ops.infra.store.json_store import JsonStateStore
from ops.core.state import FactorRecord, FactorStatus, CheckRecord


# ---------------------------------------------------------------------------
# metric_get / sort_key 逐键语义
# ---------------------------------------------------------------------------

def test_metric_get_basic_keys():
    rec = DerivedRecord(name="A", ret=15.0, shrp=2.0, mdd=8.0, tvr=40.0, fitness=1.1)
    assert metric_get(rec, "ret") == 15.0
    assert metric_get(rec, "shrp") == 2.0
    assert metric_get(rec, "mdd") == 8.0
    assert metric_get(rec, "tvr") == 40.0
    assert metric_get(rec, "fitness") == 1.1


def test_metric_get_bcorr_uses_abs():
    assert metric_get(DerivedRecord(name="A", max_bcorr=-0.9), "bcorr") == 0.9
    assert metric_get(DerivedRecord(name="A", max_bcorr=0.3), "bcorr") == 0.3


def test_metric_get_dump_days_delay_to_float():
    rec = DerivedRecord(name="A", dump_days=100, delay=1)
    assert metric_get(rec, "dump_days") == 100.0
    assert isinstance(metric_get(rec, "dump_days"), float)
    assert metric_get(rec, "delay") == 1.0


def test_metric_get_none_returns_none():
    rec = DerivedRecord(name="A")
    for k in ("ret", "shrp", "mdd", "tvr", "fitness", "bcorr", "dump_days", "delay"):
        assert metric_get(rec, k) is None


def test_sort_key_none_semantics():
    # dump_days None -> 0, 其余 None -> -inf
    assert sort_key(DerivedRecord(name="A"), "dump_days") == 0.0
    assert sort_key(DerivedRecord(name="A"), "ret") == float("-inf")
    assert sort_key(DerivedRecord(name="A"), "bcorr") == float("-inf")
    # 有值时正常
    assert sort_key(DerivedRecord(name="A", ret=12.0), "ret") == 12.0
    assert sort_key(DerivedRecord(name="A", max_bcorr=-0.8), "bcorr") == 0.8


# ---------------------------------------------------------------------------
# JsonDerivedStore
# ---------------------------------------------------------------------------

@pytest.fixture
def jderived(tmp_path):
    return JsonDerivedStore(tmp_path / "derived.json")


def test_json_derived_four_groups_independent(jderived):
    jderived.upsert_index({"A": {"author": "wbai", "has_pnl": True, "dump_days": 10, "delay": 0}})
    jderived.upsert_metrics("A", {"ret": 15.0, "shrp": 2.0, "mdd": 8.0, "tvr": 40.0, "fitness": 1.1})
    jderived.upsert_datasources("A", ["ashareeodprices.s_dq_close"], ["ashareeodprices"])
    jderived.upsert_bcorr("A", 0.5, "B")
    rec = jderived.get("A")
    # 四组都在,互不覆盖
    assert rec.author == "wbai"
    assert rec.ret == 15.0
    assert rec.fields == ["ashareeodprices.s_dq_close"]
    assert rec.max_bcorr == 0.5
    # 再 upsert metrics 不该抹掉 datasources
    jderived.upsert_metrics("A", {"ret": 20.0, "shrp": 3.0, "mdd": 5.0, "tvr": 30.0, "fitness": 1.5})
    rec = jderived.get("A")
    assert rec.ret == 20.0
    assert rec.fields == ["ashareeodprices.s_dq_close"]


def test_json_derived_delete(jderived):
    jderived.upsert_metrics("A", {"ret": 1.0, "shrp": 1.0, "mdd": 1.0, "tvr": 1.0, "fitness": 1.0})
    assert jderived.delete("A") is True
    assert jderived.get("A") is None
    assert jderived.delete("A") is False


def test_json_derived_get_all_pushdown(jderived):
    jderived.upsert_index({
        "A": {"author": "wbai", "has_pnl": True, "dump_days": 10, "delay": 0},
        "B": {"author": "mhe", "has_pnl": True, "dump_days": 20, "delay": 1},
        "C": {"author": "wbai", "has_pnl": False, "dump_days": 5, "delay": 0},
    })
    jderived.upsert_metrics("A", {"ret": 15.0, "shrp": 2.0, "mdd": 8.0, "tvr": 40.0, "fitness": 1.1})
    jderived.upsert_metrics("B", {"ret": 25.0, "shrp": 3.0, "mdd": 5.0, "tvr": 30.0, "fitness": 1.5})
    jderived.upsert_metrics("C", {"ret": 5.0, "shrp": 1.0, "mdd": 12.0, "tvr": 50.0, "fitness": 0.5})
    jderived.upsert_datasources("A", ["ashareeodprices.s_dq_close"], ["ashareeodprices"])

    # author 过滤
    assert set(jderived.get_all(author="wbai")) == {"A", "C"}
    # field 反查
    assert set(jderived.get_all(field="ashareeodprices.s_dq_close")) == {"A"}
    # table_glob
    assert set(jderived.get_all(table_glob="ashare*")) == {"A"}
    # metrics 阈值
    assert set(jderived.get_all(metrics=[("ret", ">", 10.0)])) == {"A", "B"}
    assert set(jderived.get_all(metrics=[("ret", ">=", 25.0)])) == {"B"}
    # sort_by 降序 + limit
    ordered = list(jderived.get_all(sort_by="ret"))
    assert ordered == ["B", "A", "C"]
    assert list(jderived.get_all(sort_by="ret", limit=2)) == ["B", "A"]


def test_json_derived_meta(jderived):
    assert jderived.get_meta("index_built_at") is None
    jderived.set_meta("index_built_at", "2026-07-05T00:00:00")
    assert jderived.get_meta("index_built_at") == "2026-07-05T00:00:00"


# ---------------------------------------------------------------------------
# JsonStateStore
# ---------------------------------------------------------------------------

@pytest.fixture
def jstate(tmp_path):
    return JsonStateStore(tmp_path / "factor_state.json")


def _rec(name="A", author="wbai", status=FactorStatus.SUBMITTED):
    return FactorRecord(name=name, author=author, status=status,
                        updated_at="2026-07-05T00:00:00",
                        submitted_at="2026-07-05T00:00:00", submitted_by=author)


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
    jstate.put(_rec("A", "wbai", FactorStatus.ACTIVE))
    jstate.put(_rec("B", "mhe", FactorStatus.SUBMITTED))
    jstate.put(_rec("C", "wbai", FactorStatus.SUBMITTED))
    assert {r.name for r in jstate.list(author="wbai")} == {"A", "C"}
    assert {r.name for r in jstate.list(status=FactorStatus.SUBMITTED)} == {"B", "C"}
    assert {r.name for r in jstate.list(author="wbai", status=FactorStatus.SUBMITTED)} == {"C"}
