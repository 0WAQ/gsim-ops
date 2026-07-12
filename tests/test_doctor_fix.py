"""doctor fix 五道闸行为测试(PG 组,I2 per-schema 隔离)。

铁律断言原则:每个用例除了"该删的删了",都显式断言"不碰什么"——
邻居合法文件 / ACTIVE 产物 / PG 行 fix 后原样在位。
"""
import os

import pytest

from ops.core.state import FactorStatus
from ops.services.doctor import run_doctor
from ops.services.doctor.findings import FIXED, LOCKED, VANISHED

pytestmark = pytest.mark.pg

YES = lambda *a: True  # noqa: E731 — 测试确认回调
NO = lambda *a: False  # noqa: E731


def _result(results, family_id):
    return next(r for r in results if r.family_id == family_id)


def _seed_snapshot(config, name, snapshot_at):
    """直插 snapshot 行(绕过 repo.attach_snapshot 的 entered_at 强制)——
    模拟迁移期存量的非法行。"""
    from ops.core.factor import FactorSnapshot
    from ops.infra.snapshot import default_snapshot_store
    default_snapshot_store(config).insert(
        FactorSnapshot(name=name, ret=1.0, snapshot_at=snapshot_at))


def test_readonly_never_touches_anything(test_config, seed_factor):
    """缺省(无 fix)纯只读:漂移全被发现,盘面/PG 原样。"""
    _, config = test_config
    seed_factor("AlphaGhostRej", FactorStatus.REJECTED)
    ghost = config.pnl_manual / "AlphaGhostRej"
    ghost.write_text("pnl")
    _seed_snapshot(config, "AlphaGhostRej", "2026-07-04T00:00:00")
    orphan_dump = config.alpha_dump / "AlphaNoRecord"
    orphan_dump.mkdir(parents=True)

    _, results = run_doctor(config)

    assert any(f.kind == "ghost" for f in _result(results, "pool-ghost").findings)
    assert any(f.kind == "illegal" for f in _result(results, "snapshot-stale").findings)
    assert any(f.kind == "orphan" for f in _result(results, "dump-orphan").findings)
    # 只读:全部原样
    assert ghost.exists() and orphan_dump.exists()
    from ops.infra.repository import FactorRepository
    assert FactorRepository(config).get("AlphaGhostRej").snapshot is not None


def test_fix_pool_ghost_spares_neighbors(test_config, seed_factor):
    _, config = test_config
    seed_factor("AlphaGhostRej", FactorStatus.REJECTED)
    seed_factor("AlphaLive", FactorStatus.ACTIVE)
    ghost = config.pnl_manual / "AlphaGhostRej"
    live = config.pnl_manual / "AlphaLive"
    live_pnl = config.alpha_pnl / "AlphaGhostRej"     # 禁区邻居:alpha_pnl 不是池
    ghost.write_text("pnl")
    live.write_text("pnl")
    live_pnl.write_text("pnl")

    _, results = run_doctor(config, fix=("pool-ghost",), confirm=YES)

    r = _result(results, "pool-ghost")
    assert r.fixed == 1 and not ghost.exists()
    # 不碰什么:ACTIVE 邻居副本、alpha_pnl(别名池)、PG 行
    assert live.exists() and live_pnl.exists()
    from ops.infra.repository import FactorRepository
    assert FactorRepository(config).get("AlphaGhostRej") is not None

    # 幂等:重跑零动作
    _, results2 = run_doctor(config, fix=("pool-ghost",), confirm=YES)
    assert _result(results2, "pool-ghost").fixed == 0
    assert not any(f.kind == "ghost"
                   for f in _result(results2, "pool-ghost").findings)


def test_fix_pool_ghost_skips_info_orphan(test_config, seed_factor):
    """info 孤儿(有 info 无 state)的池副本不进自动删除集(先诊断)。"""
    from ops.infra.info import FactorInfo, default_info_store
    _, config = test_config
    default_info_store(config).upsert(FactorInfo(
        name="AlphaOrphan", author="wbai", discovery_method="manual",
        created_at="2026-07-05T00:00:00"))
    copy = config.pnl_manual / "AlphaOrphan"
    copy.write_text("pnl")

    _, results = run_doctor(config, fix=("pool-ghost",), confirm=YES)

    r = _result(results, "pool-ghost")
    assert any(f.kind == "ghost-info-orphan" for f in r.findings)
    assert r.fixed == 0 and copy.exists()
    assert any(f.name == "AlphaOrphan"
               for f in _result(results, "info-orphan").findings)


def test_fix_snapshot_illegal_only(test_config, seed_factor):
    """illegal(entered_at 空)删行;合法快照(ACTIVE 对齐)绝不碰。"""
    _, config = test_config
    seed_factor("AlphaRejSnap", FactorStatus.REJECTED)
    _seed_snapshot(config, "AlphaRejSnap", "2026-07-04T00:00:00")
    seed_factor("AlphaGood", FactorStatus.ACTIVE, entered_at="2026-07-02T00:00:00")
    _seed_snapshot(config, "AlphaGood", "2026-07-02T00:00:00")

    _, results = run_doctor(config, fix=("snapshot-stale",), confirm=YES)

    assert _result(results, "snapshot-stale").fixed == 1
    from ops.infra.repository import FactorRepository
    repo = FactorRepository(config)
    assert repo.get("AlphaRejSnap").snapshot is None       # illegal 已删
    assert repo.get("AlphaGood").snapshot is not None      # 合法快照原样
    assert repo.get("AlphaRejSnap").state is not None      # state/info 行不碰


def test_fix_dump_orphan_spares_recorded(test_config, seed_factor):
    _, config = test_config
    seed_factor("AlphaKnown", FactorStatus.REJECTED)
    known = config.alpha_dump / "AlphaKnown"
    gone = config.alpha_dump / "AlphaGone"
    known.mkdir(parents=True)
    gone.mkdir(parents=True)
    (gone / "20260101.v2.npy").write_text("x")

    _, results = run_doctor(config, fix=("dump-orphan",), confirm=YES)

    assert _result(results, "dump-orphan").fixed == 1
    assert not gone.exists()
    assert known.exists()          # 有 PG 记录(哪怕 REJECTED)不碰


def test_fix_pack_tmp_stale_only(test_config, seed_factor):
    _, config = test_config
    stale = config.alpha_feature / ".AlphaOld.v2.npy.tmp"
    fresh = config.alpha_feature / ".AlphaNew.v2.npy.tmp"
    real = config.alpha_feature / "AlphaOld.v2.npy"
    for p in (stale, fresh, real):
        p.write_text("x")
    old = 1_700_000_000
    os.utime(stale, (old, old))

    _, results = run_doctor(config, fix=("artifact-orphan",), confirm=YES)

    r = _result(results, "artifact-orphan")
    assert r.fixed == 1 and not stale.exists()
    assert fresh.exists() and real.exists()   # 新鲜 tmp(在跑 pack)与正式 npy 不碰


def test_confirm_denied_means_no_action(test_config, seed_factor):
    _, config = test_config
    seed_factor("AlphaGhostRej", FactorStatus.REJECTED)
    ghost = config.pnl_manual / "AlphaGhostRej"
    ghost.write_text("pnl")

    _, results = run_doctor(config, fix=("pool-ghost",), confirm=NO)
    assert _result(results, "pool-ghost").fixed == 0 and ghost.exists()

    # confirm 缺省 None 同样视为拒绝(engine 缺省绝不动)
    _, results = run_doctor(config, fix=("pool-ghost",))
    assert _result(results, "pool-ghost").fixed == 0 and ghost.exists()


def test_toctou_recheck_blocks_stale_verdict(test_config, seed_factor):
    """扫描后、执行前因子转 ACTIVE(模拟 restage→重检入库)→ 锁内重验拒删。

    confirm 回调正好卡在扫描与执行之间 —— 在里面翻状态。
    """
    from ops.infra.store import default_store
    _, config = test_config
    seed_factor("AlphaFlip", FactorStatus.REJECTED)
    copy = config.pnl_manual / "AlphaFlip"
    copy.write_text("pnl")

    def flip_then_yes(result, fixer):
        default_store(config).transition("AlphaFlip", FactorStatus.ACTIVE,
                                         entered_at="2026-07-06T00:00:00")
        return True

    _, results = run_doctor(config, fix=("pool-ghost",), confirm=flip_then_yes)

    r = _result(results, "pool-ghost")
    assert r.fixed == 0
    assert r.fix_log and r.fix_log[0][1] == VANISHED
    assert copy.exists()           # 重新入库因子的池副本保住了


def test_locked_factor_skipped(test_config, seed_factor):
    """他人持锁(在跑的 check/rm)→ LOCKED 跳过,不删不炸。"""
    from ops.infra.lock import factor_lock
    _, config = test_config
    seed_factor("AlphaHeld", FactorStatus.REJECTED)
    copy = config.pnl_manual / "AlphaHeld"
    copy.write_text("pnl")

    with factor_lock("AlphaHeld", config):
        _, results = run_doctor(config, fix=("pool-ghost",), confirm=YES)

    r = _result(results, "pool-ghost")
    assert r.count(LOCKED) == 1 and r.fixed == 0 and copy.exists()
    assert r.residual("fail") == 1     # 锁跳过仍是余量(退出码语义)

    # 锁释放后重跑即收敛
    _, results2 = run_doctor(config, fix=("pool-ghost",), confirm=YES)
    assert _result(results2, "pool-ghost").fixed == 1 and not copy.exists()


def test_fix_log_outcomes_are_accounted(test_config, seed_factor):
    """ENOENT → VANISHED 记账(与并发 rm 抢删属正常,不算错误)。"""
    _, config = test_config
    seed_factor("AlphaGhostRej", FactorStatus.REJECTED)
    ghost = config.pnl_manual / "AlphaGhostRej"
    ghost.write_text("pnl")

    def unlink_then_yes(result, fixer):
        ghost.unlink()             # 模拟并发 ops rm 抢删
        return True

    _, results = run_doctor(config, fix=("pool-ghost",), confirm=unlink_then_yes)
    r = _result(results, "pool-ghost")
    assert r.fix_log[0][1] in (VANISHED, FIXED)   # 抢删后:重验或 unlink 见 ENOENT
    assert r.fixed == 0 or not ghost.exists()
    assert r.residual("fail") == 0                # 无论谁删的,漂移已消


def test_misconfigured_dump_root_deletes_nothing(test_config, seed_factor):
    """对抗评审 major:alpha_dump 指错到 alphalib 根 → 整族 skip 零动作,
    alpha_feature/双池一根汗毛不掉(等值闸 + 绊线双防)。"""
    _, config = test_config
    (config.alpha_feature / "AlphaGood.v2.npy").write_text("x")
    (config.pnl_manual / "AlphaGood").write_text("p")
    config.alpha_dump = config.alpha_src.parent      # 模拟 config 少写一级

    _, results = run_doctor(config, fix=("dump-orphan",), confirm=YES)

    r = _result(results, "dump-orphan")
    assert r.skip_reason and "疑似" in r.skip_reason
    assert r.fixed == 0 and not r.findings
    assert (config.alpha_feature / "AlphaGood.v2.npy").exists()
    assert (config.pnl_manual / "AlphaGood").exists()
    assert config.alpha_src.exists() and config.staging.exists()


def test_guards_block_declared_root_targets(test_config, seed_factor):
    """等值闸单点:哪怕判定/白名单都放行,目标是 config 声明的数据根本身
    → BLOCKED(绊线拦不住的 sidecar 软链错指形态由此闸兜底)。"""
    from ops.infra.repository import FactorRepository
    from ops.services.doctor import guards
    from ops.services.doctor.checks import Fixer
    from ops.services.doctor.findings import BLOCKED, Finding, FixPlan

    _, config = test_config
    fixer = Fixer(
        plan=FixPlan(action="rmtree", target="t", keeps="k"),
        resolve=lambda finding, cfg: cfg.pnl_manual,          # 目标=声明根本身
        recheck=lambda finding, factor: True,
        allowed_roots=lambda cfg: (cfg.pnl_manual.parent,),   # 白名单故意放行
    )
    finding = Finding("pnl_manual", "dump-orphan", "orphan", "warn", "r",
                      fixable=True)
    outcome, err = guards.execute(finding, fixer, config,
                                  FactorRepository(config))
    assert outcome == BLOCKED and "数据根" in err
    assert config.pnl_manual.exists()


def test_backfill_holds_factor_lock(test_config, seed_factor):
    """对抗评审 major:backfill 原是全库唯一无锁状态写入方(击穿 doctor
    TOCTOU 防线)。现在:锁被持有 → 跳过不 register;锁放开 → 正常补录。"""
    from types import SimpleNamespace

    from ops.core.factormeta import FactorMeta
    from ops.infra.lock import factor_lock
    from ops.infra.repository import FactorRepository
    from ops.services.backfill.backfill import run_backfill

    cfg_path, config = test_config
    d = config.alpha_src / "AlphaWbaiLegacy"
    d.mkdir(parents=True)
    FactorMeta(name="AlphaWbaiLegacy", author="wbai", birthday=20240101,
               universe="all", category="misc", delay=1, backdays=30,
               dump_alpha=True, has_intraday_curve=False).save(d / "meta.json")
    args = SimpleNamespace(config_path=cfg_path, dry_run=False)
    repo = FactorRepository(config)

    with factor_lock("AlphaWbaiLegacy", config):
        run_backfill(args)
    assert repo.get("AlphaWbaiLegacy") is None       # 锁内:跳过,零写

    run_backfill(args)
    factor = repo.get("AlphaWbaiLegacy")             # 锁放开:正常补录 ACTIVE
    assert factor is not None and factor.state is not None
    assert factor.state.status == FactorStatus.ACTIVE
