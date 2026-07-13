"""doctor 判定纯函数 + 注册表约束(零 I/O 零 PG,Inventory 手造表驱动)。

fix 执行(五道闸)的行为测试在 test_doctor_fix.py(PG 组)。
"""
from pathlib import Path

from ops.core.factor import Factor, FactorIdentity, FactorSnapshot
from ops.core.state import FactorRecord, FactorStatus
from ops.services.doctor.checks import FAMILIES, FIXABLE_IDS, classify_pool
from ops.services.doctor.findings import (
    FAIL,
    FIXED,
    LOCKED,
    WARN,
    Area,
    Entry,
    FamilyResult,
    Finding,
    Inventory,
)

NOW = 1_800_000_000.0
DAY = 86400.0


def _factor(name, status=None, dm="manual", entered_at=None, snapshot_at=None,
            author="wbai", created_at=None, submitted_at=None):
    state = None
    if status is not None:
        state = FactorRecord(name=name, status=status,
                             updated_at="2026-07-05T00:00:00",
                             entered_at=entered_at,
                             submitted_at=submitted_at)
    snapshot = None
    if snapshot_at is not None:
        snapshot = FactorSnapshot(name=name, snapshot_at=snapshot_at)
    return Factor(identity=FactorIdentity(name=name, author=author,
                                          discovery_method=dm,
                                          created_at=created_at),
                  state=state, snapshot=snapshot)


def _inv(factors=(), **areas) -> Inventory:
    """全区缺省为空;areas 传 {area: [Entry|str]}(str 便捷 = 文件条目)。"""
    all_areas = {}
    for key in ("alpha_src", "staging", "alpha_pnl", "alpha_feature",
                "pool_automated", "pool_manual", "dump_local"):
        raw = areas.get(key, [])
        entries = [e if isinstance(e, Entry) else Entry(name=e, is_dir=False)
                   for e in raw]
        all_areas[key] = Area(root=Path(f"/fake/{key}"), entries=entries)
    return Inventory(factors={f.identity.name: f for f in factors},
                     areas=all_areas, hostname="testhost", now=NOW)


def _scan(family_id, inv):
    family = next(f for f in FAMILIES if f.family_id == family_id)
    return family.scan(inv)


def _kinds(findings):
    return {(f.name, f.kind) for f in findings}


# ---------------------------------------------------------------- pool-ghost

def test_classify_pool_verdict_table():
    """判定表原样移植自 reconcile_bcorr_pools(生产 622 清零实证,不改语义)。"""
    assert classify_pool(None, "manual")[0] == "ghost"
    assert classify_pool(_factor("A"), "manual")[0] == "ghost"          # info 孤儿
    assert classify_pool(_factor("A", FactorStatus.REJECTED), "manual")[0] == "ghost"
    assert classify_pool(_factor("A", FactorStatus.ACTIVE, dm="automated"),
                         "manual")[0] == "wrong-pool"
    assert classify_pool(_factor("A", FactorStatus.ACTIVE, dm="manual"),
                         "manual") == ("ok", "")


def test_pool_ghost_scan_kinds():
    inv = _inv(
        factors=[
            _factor("AlphaOk", FactorStatus.ACTIVE, dm="manual"),
            _factor("AlphaRej", FactorStatus.REJECTED, dm="manual"),
            _factor("AlphaInfoOrphan"),                       # state None
            _factor("AlphaWrong", FactorStatus.ACTIVE, dm="automated"),
            _factor("AlphaMissing", FactorStatus.ACTIVE, dm="manual"),  # 无副本
        ],
        pool_manual=["AlphaOk", "AlphaRej", "AlphaInfoOrphan", "AlphaWrong",
                     "AlphaNoRecord"],
    )
    findings = _scan("pool-ghost", inv)
    kinds = _kinds(findings)
    assert ("AlphaRej", "ghost") in kinds
    assert ("AlphaNoRecord", "ghost") in kinds
    # info 孤儿从自动删除集剔除:kind 单列且不可修(先诊断后删证据)
    assert ("AlphaInfoOrphan", "ghost-info-orphan") in kinds
    assert not next(f for f in findings if f.name == "AlphaInfoOrphan").fixable
    assert ("AlphaWrong", "wrong-pool") in kinds
    assert ("AlphaMissing", "missing") in kinds
    assert ("AlphaOk", "ghost") not in kinds
    # ghost 是 FAIL 且可修;wrong-pool/missing 只报告
    ghost = next(f for f in findings if f.kind == "ghost" and f.name == "AlphaRej")
    assert ghost.severity == FAIL and ghost.fixable and ghost.ref == "manual"
    assert all(not f.fixable for f in findings if f.kind in ("wrong-pool", "missing"))


# ------------------------------------------------------------ snapshot-stale

def test_snapshot_stale_kinds():
    """v3 测得快照:snapshot_at 锚最近一次 check 事件 at(无事件锚 entered_at)。
    被拒因子带快照 + 锚点吻合 = 合法(原 illegal kind 作废);全族只报告。"""
    inv = _inv(factors=[
        # 被拒 + 快照锚定其 check 事件 → 合法(v3 的核心新形态)
        _factor("AlphaRejMeasured", FactorStatus.REJECTED,
                snapshot_at="2026-07-04T00:00:00"),
        # 被拒 + 快照时间戳与 check 事件不符 → mismatch
        _factor("AlphaRejDrift", FactorStatus.REJECTED,
                snapshot_at="2026-07-04T00:00:00"),
        # legacy 无 check 事件:锚 entered_at
        _factor("AlphaLegacyClean", FactorStatus.ACTIVE,
                entered_at="2026-07-02T00:00:00", snapshot_at="2026-07-02T00:00:00"),
        _factor("AlphaLegacyDrift", FactorStatus.ACTIVE,
                entered_at="2026-07-02T00:00:00", snapshot_at="2026-07-04T00:00:00"),
        # 无锚:无事件且 entered_at 空
        _factor("AlphaUnanchored", FactorStatus.REJECTED,
                snapshot_at="2026-07-04T00:00:00"),
        _factor("AlphaNoSnap", FactorStatus.ACTIVE),
    ])
    inv.last_check_at = {"AlphaRejMeasured": "2026-07-04T00:00:00",
                         "AlphaRejDrift": "2026-07-05T00:00:00"}
    findings = _scan("snapshot-stale", inv)
    assert _kinds(findings) == {("AlphaRejDrift", "mismatch"),
                                ("AlphaLegacyDrift", "mismatch"),
                                ("AlphaUnanchored", "unanchored")}
    assert not any(f.fixable for f in findings)   # 全族只报告,修正归一次性脚本


# ------------------------------------------------------------ timeline-drift

def test_timeline_drift_invariant():
    """词汇表不变量 created_at <= submitted_at(legacy 清理批顺手项):
    只有两值俱在且 created > submitted 才报;backfill 存量 submitted_at=NULL
    是设计内值,跳过;相等(首提逐字符相等)与正常先后合法。全族只报告。"""
    inv = _inv(factors=[
        _factor("AlphaViolate", FactorStatus.ACTIVE,
                created_at="2026-07-10T02:00:00",
                submitted_at="2026-07-08T10:00:00"),
        _factor("AlphaEqual", FactorStatus.ACTIVE,
                created_at="2026-07-08T10:00:00",
                submitted_at="2026-07-08T10:00:00"),
        _factor("AlphaNormal", FactorStatus.REJECTED,
                created_at="2026-07-01T10:00:00",
                submitted_at="2026-07-08T10:00:00"),
        _factor("AlphaLegacyNull", FactorStatus.ACTIVE,
                created_at="2026-07-06T16:38:27"),          # submitted_at=NULL
        _factor("AlphaInfoOrphan", created_at="2026-07-10T02:00:00"),  # 无 state
    ])
    findings = _scan("timeline-drift", inv)
    assert _kinds(findings) == {("AlphaViolate", "created-after-submitted")}
    assert all(f.severity == WARN and not f.fixable for f in findings)


# --------------------------------------------------------------- info-orphan

def test_info_orphan_routing():
    inv = _inv(
        factors=[_factor("AlphaBare"), _factor("AlphaWithSrc"),
                 _factor("AlphaFine", FactorStatus.ACTIVE)],
        alpha_src=[Entry("AlphaWithSrc", is_dir=True)],
    )
    findings = _scan("info-orphan", inv)
    assert _kinds(findings) == {("AlphaBare", "orphan"), ("AlphaWithSrc", "orphan")}
    assert all(f.severity == FAIL and not f.fixable for f in findings)
    bare = next(f for f in findings if f.name == "AlphaBare")
    withsrc = next(f for f in findings if f.name == "AlphaWithSrc")
    # 无产物 → 可贴命令;不带 -y(rm 自己的交互确认留作最后人闸)
    assert bare.action == "ops rm AlphaBare"
    assert "人工判读" in withsrc.action                 # 有产物 → 人工


def test_info_orphan_blind_area_never_suggests_rm():
    """盘面区不可读时"看不见产物"≠"没有产物"—— 绝不生成删除转介
    (对抗评审 2026-07-12:盲区下的 ops rm 转介会经人手级联删 alpha_src)。"""
    inv = _inv(factors=[_factor("AlphaMaybe")])       # info 孤儿,盘面"看不见"
    inv.areas["alpha_src"].error = "PermissionError: [Errno 13]"
    findings = _scan("info-orphan", inv)
    action = findings[0].action
    assert "ops rm" not in action
    assert "不可读" in action and "src" in action


# ----------------------------------------------------------------- src-drift

def test_src_drift_kinds():
    inv = _inv(
        factors=[
            _factor("AlphaActive", FactorStatus.ACTIVE),      # 无目录 → lib-missing
            _factor("AlphaRej", FactorStatus.REJECTED),       # 无目录 → lib-missing
            _factor("AlphaSubmitted", FactorStatus.SUBMITTED),  # 未入库,不报
            _factor("AlphaHasDir", FactorStatus.ACTIVE),
        ],
        alpha_src=[Entry("AlphaHasDir", is_dir=True),
                   Entry("AlphaStray", is_dir=True),
                   Entry("notes.txt", is_dir=False)],          # 非目录不算孤儿
    )
    findings = _scan("src-drift", inv)
    assert _kinds(findings) == {("AlphaActive", "lib-missing"),
                                ("AlphaRej", "lib-missing"),
                                ("AlphaStray", "src-orphan")}
    assert all(not f.fixable for f in findings)   # v1 铁律:alpha_src 零删除
    assert next(f for f in findings if f.kind == "lib-missing").severity == FAIL
    assert next(f for f in findings if f.kind == "src-orphan").severity == WARN


def test_src_drift_crossreferences_staging():
    """recall 是 move:restage 崩在 transition 前,唯一副本在 staging ——
    报 lib-missing-staged 指向 staging,勿指去 dropbox 反查(对抗评审)。
    staging 区不可读时回退 lib-missing(不敢断言副本在)。"""
    inv = _inv(
        factors=[_factor("AlphaStuck", FactorStatus.ACTIVE)],
        staging=[Entry("AlphaStuck", is_dir=True)],
    )
    findings = _scan("src-drift", inv)
    assert _kinds(findings) == {("AlphaStuck", "lib-missing-staged")}
    assert findings[0].severity == WARN and "staging" in findings[0].action

    inv.areas["staging"].error = "PermissionError"
    findings = _scan("src-drift", inv)
    assert _kinds(findings) == {("AlphaStuck", "lib-missing")}   # 盲区回退


# ------------------------------------------------------------- staging-drift

def test_staging_drift_kinds():
    inv = _inv(
        factors=[
            _factor("AlphaQueued", FactorStatus.SUBMITTED),   # 无目录 → missing-dir
            _factor("AlphaInflight", FactorStatus.CHECKING),  # 有目录,不报
            _factor("AlphaActive", FactorStatus.ACTIVE),      # 不在 staging 语义内
        ],
        staging=[Entry("AlphaInflight", is_dir=True),
                 Entry("AlphaCrash", is_dir=True)],           # 无记录 → orphan-dir
    )
    findings = _scan("staging-drift", inv)
    assert _kinds(findings) == {("AlphaCrash", "orphan-dir"),
                                ("AlphaQueued", "missing-dir")}
    # 转介既有命令,doctor 不做第二套删除
    assert all(not f.fixable for f in findings)
    assert next(f for f in findings if f.kind == "orphan-dir").action == "ops clear AlphaCrash"
    assert "--force" in next(f for f in findings if f.kind == "missing-dir").action


# ----------------------------------------------------------- artifact-orphan

def test_artifact_orphan_kinds():
    inv = _inv(
        factors=[_factor("AlphaKnown", FactorStatus.ACTIVE)],
        alpha_pnl=["AlphaKnown", "AlphaGhostPnl",
                   Entry("AlphaDirPnl", is_dir=True)],
        alpha_feature=[
            "AlphaKnown.v2.npy",
            "AlphaGhostFeat.v1.npy",
            "weird.bin",
            Entry("somedir", is_dir=True),
            Entry(".AlphaOld.v2.npy.tmp", is_dir=False, mtime=NOW - 2 * DAY),
            Entry(".AlphaFresh.v2.npy.tmp", is_dir=False, mtime=NOW - 3600),
        ],
    )
    findings = _scan("artifact-orphan", inv)
    kinds = _kinds(findings)
    assert ("AlphaGhostPnl", "pnl-orphan") in kinds
    assert ("AlphaGhostFeat", "feature-orphan") in kinds
    assert ("weird.bin", "alien") in kinds
    assert ("AlphaOld", "pack-tmp") in kinds
    # 目录形态不静默吞(布局 SSOT:pnl/feature 是单文件;对抗评审)
    assert ("AlphaDirPnl", "alien") in kinds
    assert ("somedir", "alien") in kinds
    # 新鲜 tmp(在跑的 pack)不报
    assert not any(f.name == "AlphaFresh" for f in findings)
    # v1.1 放闸(2026-07-12 基线判读后):feature-orphan 可修;
    # pnl-orphan / alien 仍只报告(无判读材料)
    assert not any(f.fixable for f in findings
                   if f.kind not in ("pack-tmp", "feature-orphan"))
    feat = next(f for f in findings if f.kind == "feature-orphan")
    assert feat.fixable and feat.ref == "AlphaGhostFeat.v1.npy"
    tmp = next(f for f in findings if f.kind == "pack-tmp")
    assert tmp.fixable and tmp.ref == ".AlphaOld.v2.npy.tmp"


# ---------------------------------------------------------------- dump-orphan

def test_dump_orphan_scan():
    inv = _inv(
        factors=[_factor("AlphaKnown", FactorStatus.REJECTED)],  # 有记录即不算孤儿
        dump_local=[Entry("AlphaKnown", is_dir=True),
                    Entry("AlphaGone", is_dir=True),
                    Entry("stray.file", is_dir=False)],          # 非目录不报
    )
    findings = _scan("dump-orphan", inv)
    assert _kinds(findings) == {("AlphaGone", "orphan")}
    assert findings[0].fixable


def test_dump_orphan_misconfig_tripwire():
    """alpha_dump 指错一级 → 扫到的是 alphalib 根,条目撞库区名 → 整族弃权
    零发现,绝不把 alpha_feature/双池判成 fixable 孤儿(对抗评审 major)。"""
    import pytest as _pytest

    from ops.services.doctor.findings import FamilySkip

    inv = _inv(dump_local=[Entry("alpha_src", is_dir=True),
                           Entry("alpha_feature", is_dir=True),
                           Entry("pnl_automated", is_dir=True),
                           Entry("AlphaReal", is_dir=True)])
    with _pytest.raises(FamilySkip):
        _scan("dump-orphan", inv)


# ------------------------------------------------------- 注册表约束 + 记账

def test_registry_invariants():
    """fixer 白名单 action、FixPlan 三句话必填非空(打印的就是执行的)。"""
    # v3:snapshot-stale 退出可修集(测得快照语义下原 illegal 修复对象
    # 变成合法形态,fixer 退役 —— 误留会删掉被拒因子的测得快照)
    assert set(FIXABLE_IDS) == {"pool-ghost", "artifact-orphan", "dump-orphan"}
    for family in FAMILIES:
        assert family.scope in ("pg", "global", "host")
        if family.fixer is None:
            continue
        plan = family.fixer.plan
        assert plan.action in ("unlink", "rmtree", "discard_snapshot")
        assert plan.target and plan.keeps


def test_family_result_residual_accounting():
    f1 = Finding("A", "pool-ghost", "ghost", FAIL, "r", fixable=True)
    f2 = Finding("B", "pool-ghost", "ghost", FAIL, "r", fixable=True)
    f3 = Finding("C", "pool-ghost", "wrong-pool", WARN, "r")
    fr = FamilyResult("pool-ghost", "t", "global", population=3,
                      findings=[f1, f2, f3])
    assert fr.residual(FAIL) == 2
    fr.fix_log.append((f1, FIXED, ""))
    fr.fix_log.append((f2, LOCKED, "held"))
    assert fr.residual(FAIL) == 1          # 锁跳过的仍是余量
    assert fr.fixed == 1 and fr.count(LOCKED) == 1


def test_doctor_write_declaration():
    """doctor 缺省只读(is_write_command=False,不 sudo);--fix 经 _FixAction
    置 True(S16 声明机制的 setup _CheckAction 反相)。"""
    import argparse

    from ops.cli.doctor import add_doctor_subparser

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="sub-command")
    add_doctor_subparser(sub)

    args = parser.parse_args(["doctor"])
    assert args.is_write_command is False
    args = parser.parse_args(["doctor", "--fix", "pool-ghost"])
    assert args.is_write_command is True
    assert args.fix == ["pool-ghost"]
