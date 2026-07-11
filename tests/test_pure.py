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


def test_metric_registry_is_single_source():
    """S8:metric 事实族(键集 + 取值语义)唯一定义在 core/metrics.SNAPSHOT_METRICS,
    SQL 下推表达式 / 内存取值 / CLI choices 三个消费方全部派生。钉死两件事:
    键集决策本身,和 bcorr 的 abs 语义在 SQL/内存两半逐位一致。"""
    from ops.cli.common import METRIC_SORT_KEYS
    from ops.core.factor import FactorSnapshot
    from ops.core.metrics import SNAPSHOT_METRICS, metric_value
    from ops.infra.snapshot.pg_store import _prefixed_metric_expr, metric_order_expr

    # 键集决策(新增/删除 metric 键须有意为之)
    assert set(SNAPSHOT_METRICS) == {"ret", "shrp", "mdd", "tvr", "fitness", "bcorr"}
    assert tuple(METRIC_SORT_KEYS) == tuple(SNAPSHOT_METRICS)   # CLI choices 同源

    # SQL 半边:每个注册键都能产出下推表达式,bcorr 是 abs(带前缀改写正确)
    for key in SNAPSHOT_METRICS:
        assert _prefixed_metric_expr(key, "n.") is not None
    assert _prefixed_metric_expr("bcorr", "n.") == "abs(n.max_bcorr)"
    assert _prefixed_metric_expr("ret", "") == "ret"
    assert metric_order_expr("delay") is None                   # 白名单外拒绝

    # 内存半边:同一注册表取值,bcorr 取绝对值,与 SQL abs() 语义一致
    snap = FactorSnapshot(name="X", ret=12.5, max_bcorr=-0.42)
    assert metric_value(snap, "ret") == 12.5
    assert metric_value(snap, "bcorr") == pytest.approx(0.42)
    assert metric_value(snap, "shrp") is None                   # 值缺失
    assert metric_value(snap, "delay") is None                  # 未注册键
    assert metric_value(None, "ret") is None                    # 无快照


def test_dumpscan_layout_and_order(tmp_path):
    """dumpscan(2026-07-11 自 AlphaMetadata 迁出,core 去 I/O):
    YYYY/MM 布局按时序、非日期目录忽略、目录不存在返回空/None。
    last_v2npy_file 只看最新月份,该月无 v2 → None(**不回退更早月份**:
    checkpoint 比对的是本次运行刚写出的 dump,回退会卷进陈旧残留 ——
    None → CheckSkip 是正确路由,评审确认保持原语义)。"""
    from ops.services.check.checker.dumpscan import last_v2npy_file, v2npy_files

    missing = tmp_path / "nope"
    assert v2npy_files(missing) == [] and last_v2npy_file(missing) is None

    d = tmp_path / "AlphaX"
    for rel in ["2024/12/20241230.v2.npy", "2025/01/20250102.v2.npy",
                "2025/01/20250103.v2.npy"]:
        f = d / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.touch()
    (d / "logs").mkdir()                       # 非年份目录忽略
    (d / "2025" / "tmp").mkdir()               # 非月份目录忽略

    files = v2npy_files(d)
    assert [f.name for f in files] == [
        "20241230.v2.npy", "20250102.v2.npy", "20250103.v2.npy"]
    assert last_v2npy_file(d).name == "20250103.v2.npy"

    # 最新月份(2025/02)只有 v1 → None,不回退 2025/01
    v1only = d / "2025" / "02" / "20250201.v1.npy"
    v1only.parent.mkdir(parents=True)
    v1only.touch()
    assert last_v2npy_file(d) is None


def test_write_command_declarations_match_registry():
    """S16:写命令集由注册处 mark_write 声明派生(sudo 提权据此),不再手抄。
    经 ops.main.SUBPARSER_REGISTRARS(注册表单一正主)注册全部子命令 ——
    新命令加进 main 的注册表即自动进入本断言;唯一钉死的是这 10 个名字的
    决策本身(含 combo 有意不声明写性)。"""
    import argparse

    from ops.main import SUBPARSER_REGISTRARS

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="sub-command")
    for add_subparser in SUBPARSER_REGISTRARS:
        add_subparser(sub)

    declared = {name for name, p in sub.choices.items()
                if p.get_default("is_write_command")}
    assert declared == {"submit", "restage", "check", "run", "rm",
                        "approve", "cancel", "clear", "pack", "backfill"}
