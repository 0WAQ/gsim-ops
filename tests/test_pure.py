"""纯函数 + json 回退后端单测 (无需 PG)。

覆盖:
- JsonStateStore CRUD (单机 dev/test 后端正确性;derived 层测试随层删除, Wave 2)
"""
import pytest

from ops.core.state import CheckRecord, FactorRecord, FactorStatus
from ops.infra.store.json_store import JsonStateStore

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


# ---------------------------------------------------------------------------
# list 过滤解析 + snapshot glob→LIKE 下推(2026-07-09 阶段 2 对抗评审修复)
# ---------------------------------------------------------------------------

def test_parse_filters_rejects_bad_operator():
    """typo 比较符(=> 等)必须响亮拒绝 —— 原先能过正则但下推白名单与内存
    if 链都没有分支,条件被静默吞掉且新旧读路径因子集不一致。"""
    from ops.services.list.list import parse_filters

    assert parse_filters("ret=>30") is None      # typo → 报错返回 None
    assert parse_filters("ret=<30") is None
    assert parse_filters("shrp><1") is None
    assert parse_filters("ret>=30") == [("ret", ">=", "30")]   # 合法原样通过
    assert parse_filters("tables=ashare*,shrp>2") == [
        ("tables", "=", "ashare*"), ("shrp", ">", "2")]


def test_glob_to_like_pushdown_is_prefilter_only():
    """glob→LIKE 只许更宽不许更窄(下推是预筛,内存 fnmatch 兜底只能收窄;
    full-review S9):? 精确译成 _、字面 %/_ 转义、[seq] 放弃下推。"""
    from ops.infra.snapshot.pg_store import _glob_to_like, snapshot_where

    assert _glob_to_like("ashare*") == "ashare%"
    assert _glob_to_like("ashare?daily") == "ashare_daily"     # ? → _(单字符)
    assert _glob_to_like("a_b*") == r"a\_b%"                   # 字面 _ 转义
    assert _glob_to_like("a%b") == r"a\%b"                     # 字面 % 转义
    assert _glob_to_like("cn[ab]*") is None                    # 字符类 → 跳过下推

    clauses, params = snapshot_where(None, "cn[ab]*", None)
    assert clauses == [] and params == []                      # 整体不产出 tables 子句
