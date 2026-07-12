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
            author="wbai"):
    state = None
    if status is not None:
        state = FactorRecord(name=name, status=status,
                             updated_at="2026-07-05T00:00:00",
                             entered_at=entered_at)
    snapshot = None
    if snapshot_at is not None:
        snapshot = FactorSnapshot(name=name, snapshot_at=snapshot_at)
    return Factor(identity=FactorIdentity(name=name, author=author,
                                          discovery_method=dm),
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
    inv = _inv(factors=[
        _factor("AlphaIllegal", FactorStatus.REJECTED,
                snapshot_at="2026-07-04T00:00:00"),               # entered None
        _factor("AlphaMismatch", FactorStatus.ACTIVE,
                entered_at="2026-07-02T00:00:00", snapshot_at="2026-07-04T00:00:00"),
        _factor("AlphaClean", FactorStatus.ACTIVE,
                entered_at="2026-07-02T00:00:00", snapshot_at="2026-07-02T00:00:00"),
        _factor("AlphaNoSnap", FactorStatus.ACTIVE),
    ])
    findings = _scan("snapshot-stale", inv)
    assert _kinds(findings) == {("AlphaIllegal", "illegal"),
                                ("AlphaMismatch", "mismatch")}
    illegal = next(f for f in findings if f.kind == "illegal")
    mismatch = next(f for f in findings if f.kind == "mismatch")
    assert illegal.fixable and not mismatch.fixable   # mismatch 归一次性迁移脚本


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
    assert bare.action == "ops rm AlphaBare -y"        # 无产物 → 可贴命令
    assert "人工判读" in withsrc.action                 # 有产物 → 人工


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
        alpha_pnl=["AlphaKnown", "AlphaGhostPnl"],
        alpha_feature=[
            "AlphaKnown.v2.npy",
            "AlphaGhostFeat.v1.npy",
            "weird.bin",
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
    # 新鲜 tmp(在跑的 pack)不报;正式 npy / pnl 孤儿 v1 一律不可修
    assert not any(f.name == "AlphaFresh" for f in findings)
    assert not any(f.fixable for f in findings if f.kind != "pack-tmp")
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


# ------------------------------------------------------- 注册表约束 + 记账

def test_registry_invariants():
    """fixer 白名单 action、FixPlan 三句话必填非空(打印的就是执行的)。"""
    assert set(FIXABLE_IDS) == {"pool-ghost", "snapshot-stale",
                                "artifact-orphan", "dump-orphan"}
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
