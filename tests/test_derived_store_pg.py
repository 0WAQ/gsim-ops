"""derived 存储层单测 (PG, ops_test 库)。

覆盖 PostgresDerivedStore:四组独立 upsert 互不覆盖、get_all 各下推参
(author/has_index/field GIN/table_glob/metrics 各 op/sort_by/limit)、delete、meta。
"""
import pytest

pytestmark = pytest.mark.pg


def _seed(store):
    store.upsert_index({
        "A": {"author": "wbai", "has_pnl": True, "dump_days": 10, "delay": 0},
        "B": {"author": "mhe", "has_pnl": True, "dump_days": 20, "delay": 1},
        "C": {"author": "wbai", "has_pnl": False, "dump_days": 5, "delay": 0},
    })
    store.upsert_metrics("A", {"ret": 15.0, "shrp": 2.0, "mdd": 8.0, "tvr": 40.0, "fitness": 1.1})
    store.upsert_metrics("B", {"ret": 25.0, "shrp": 3.0, "mdd": 5.0, "tvr": 30.0, "fitness": 1.5})
    store.upsert_metrics("C", {"ret": 5.0, "shrp": 1.0, "mdd": 12.0, "tvr": 50.0, "fitness": 0.5})
    store.upsert_datasources("A", ["ashareeodprices.s_dq_close"], ["ashareeodprices"])
    store.upsert_datasources("B", ["AShareMoneyFlow.net_inflow"], ["AShareMoneyFlow"])
    store.upsert_bcorr("A", 0.5, "B")
    store.upsert_bcorr("B", -0.9, "A")


def test_four_groups_independent(derived_store):
    derived_store.upsert_index({"A": {"author": "wbai", "has_pnl": True, "dump_days": 10, "delay": 0}})
    derived_store.upsert_metrics("A", {"ret": 15.0, "shrp": 2.0, "mdd": 8.0, "tvr": 40.0, "fitness": 1.1})
    derived_store.upsert_datasources("A", ["f1"], ["t1"])
    derived_store.upsert_bcorr("A", 0.5, "B")
    rec = derived_store.get("A")
    assert rec.author == "wbai" and rec.ret == 15.0
    assert rec.fields == ["f1"] and rec.max_bcorr == 0.5
    # 再 upsert metrics 不抹 datasources / index
    derived_store.upsert_metrics("A", {"ret": 20.0, "shrp": 3.0, "mdd": 5.0, "tvr": 30.0, "fitness": 1.5})
    rec = derived_store.get("A")
    assert rec.ret == 20.0
    assert rec.fields == ["f1"]
    assert rec.author == "wbai"


def test_get_all_author(derived_store):
    _seed(derived_store)
    assert set(derived_store.get_all(author="wbai")) == {"A", "C"}


def test_get_all_has_index(derived_store):
    # 只有 index 组 (author 非空) 的行才算 has_index
    derived_store.upsert_metrics("Orphan", {"ret": 1.0, "shrp": 1.0, "mdd": 1.0, "tvr": 1.0, "fitness": 1.0})
    derived_store.upsert_index({"A": {"author": "wbai", "has_pnl": True, "dump_days": 1, "delay": 0}})
    got = derived_store.get_all(has_index=True)
    assert "A" in got
    assert "Orphan" not in got  # author 为空


def test_get_all_field_gin(derived_store):
    _seed(derived_store)
    assert set(derived_store.get_all(field="ashareeodprices.s_dq_close")) == {"A"}
    assert set(derived_store.get_all(field="AShareMoneyFlow.net_inflow")) == {"B"}


def test_get_all_table_glob(derived_store):
    _seed(derived_store)
    assert set(derived_store.get_all(table_glob="ashare*")) == {"A"}
    assert set(derived_store.get_all(table_glob="AShare*")) == {"B"}


def test_get_all_metrics_ops(derived_store):
    _seed(derived_store)
    assert set(derived_store.get_all(metrics=[("ret", ">", 10.0)])) == {"A", "B"}
    assert set(derived_store.get_all(metrics=[("ret", ">=", 25.0)])) == {"B"}
    assert set(derived_store.get_all(metrics=[("ret", "<", 10.0)])) == {"C"}
    assert set(derived_store.get_all(metrics=[("ret", "<=", 15.0)])) == {"A", "C"}
    assert set(derived_store.get_all(metrics=[("shrp", "=", 3.0)])) == {"B"}
    # bcorr 用 abs
    assert set(derived_store.get_all(metrics=[("bcorr", ">", 0.7)])) == {"B"}


def test_get_all_sort_and_limit(derived_store):
    _seed(derived_store)
    assert list(derived_store.get_all(sort_by="ret")) == ["B", "A", "C"]
    assert list(derived_store.get_all(sort_by="ret", limit=2)) == ["B", "A"]


def test_delete(derived_store):
    derived_store.upsert_metrics("A", {"ret": 1.0, "shrp": 1.0, "mdd": 1.0, "tvr": 1.0, "fitness": 1.0})
    assert derived_store.delete("A") is True
    assert derived_store.get("A") is None
    assert derived_store.delete("A") is False


def test_meta(derived_store):
    assert derived_store.get_meta("index_built_at") is None
    derived_store.set_meta("index_built_at", "2026-07-05T00:00:00")
    assert derived_store.get_meta("index_built_at") == "2026-07-05T00:00:00"
    # 覆盖更新
    derived_store.set_meta("index_built_at", "2026-07-06T00:00:00")
    assert derived_store.get_meta("index_built_at") == "2026-07-06T00:00:00"
