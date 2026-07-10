"""cancel / approve / clear / rm 写路径测试 (PG)。

- cancel: SUBMITTED (--force + CHECKING) 删 staging + 硬删 state;其他状态拒绝
- approve: 仅 correlation-rejected → ACTIVE;其他失败阶段 / 非 REJECTED 拒绝
- clear:  仅 staging 孤儿 (state 无 record);有 record 报错让用 cancel
- rm:     硬删全部落点 (src/pnl/dump/feature + factor_info 级联 state/snapshot)
"""
from types import SimpleNamespace

import pytest

from ops.core.state import FactorStatus

pytestmark = pytest.mark.pg


def _store(config):
    from ops.infra.store import default_store
    return default_store(config)


def _snapshot(config):
    from ops.infra.snapshot import default_snapshot_store
    return default_snapshot_store(config)


def _args(cfg_path, **kw):
    d = dict(config_path=cfg_path, yes=True, user=None, factor_name=None)
    d.update(kw)
    return SimpleNamespace(**d)


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------

def test_cancel_submitted(test_config, seed_factor):
    from ops.services.cancel.cancel import run_cancel
    cfg_path, config = test_config
    (config.staging / "AlphaWbaiCan").mkdir(parents=True, exist_ok=True)
    seed_factor("AlphaWbaiCan", FactorStatus.SUBMITTED)
    run_cancel(_args(cfg_path, factor_name="AlphaWbaiCan", force=False))
    # staging 删 + state 硬删
    assert not (config.staging / "AlphaWbaiCan").exists()
    assert _store(config).get("AlphaWbaiCan") is None


def test_cancel_checking_needs_force(test_config, seed_factor):
    from ops.services.cancel.cancel import run_cancel
    cfg_path, config = test_config
    (config.staging / "AlphaWbaiChk").mkdir(parents=True, exist_ok=True)
    seed_factor("AlphaWbaiChk", FactorStatus.CHECKING)
    # 不带 --force → CHECKING 不被 cancel
    run_cancel(_args(cfg_path, factor_name="AlphaWbaiChk", force=False))
    assert _store(config).get("AlphaWbaiChk") is not None
    # 带 --force → 清掉
    run_cancel(_args(cfg_path, factor_name="AlphaWbaiChk", force=True))
    assert _store(config).get("AlphaWbaiChk") is None


def test_cancel_active_rejected(test_config, seed_factor):
    from ops.services.cancel.cancel import run_cancel
    cfg_path, config = test_config
    seed_factor("AlphaWbaiActC", FactorStatus.ACTIVE)
    run_cancel(_args(cfg_path, factor_name="AlphaWbaiActC", force=False))
    # ACTIVE 不能 cancel → 保留
    assert _store(config).get("AlphaWbaiActC").status == FactorStatus.ACTIVE


def test_cancel_refuses_archived_artifacts(test_config, seed_factor):
    """曾被 check 归档的因子(如 REJECTED 后 overwrite 召回,entered_at 为空但
    alpha_src 有归档)不得 cancel —— 只删记录会留孤儿产物(2026-07-09 生产实测,
    JOURNAL U3)。"""
    from ops.services.cancel.cancel import run_cancel
    cfg_path, config = test_config
    name = "AlphaWbaiExRej"
    (config.alpha_src / name).mkdir(parents=True, exist_ok=True)
    (config.staging / name).mkdir(parents=True, exist_ok=True)
    seed_factor(name, FactorStatus.SUBMITTED)   # entered_at 为空(从未入库)
    run_cancel(_args(cfg_path, factor_name=name, force=False))
    # 拒绝:记录保留、staging 保留、归档保留
    assert _store(config).get(name) is not None
    assert (config.staging / name).exists()
    assert (config.alpha_src / name).exists()


def test_cancel_batch_skips_archived_artifacts(test_config, seed_factor):
    """批量模式下同款守卫走 skipped 通道,不阻断其它可 cancel 因子。"""
    from ops.services.cancel.cancel import run_cancel
    cfg_path, config = test_config
    # 一个纯新提交(可删) + 一个有归档(跳过)
    (config.staging / "AlphaWbaiPure").mkdir(parents=True, exist_ok=True)
    seed_factor("AlphaWbaiPure", FactorStatus.SUBMITTED)
    (config.alpha_src / "AlphaWbaiArch").mkdir(parents=True, exist_ok=True)
    (config.staging / "AlphaWbaiArch").mkdir(parents=True, exist_ok=True)
    seed_factor("AlphaWbaiArch", FactorStatus.SUBMITTED)
    run_cancel(_args(cfg_path, user="wbai", force=False))
    assert _store(config).get("AlphaWbaiPure") is None          # 删了
    assert _store(config).get("AlphaWbaiArch") is not None      # 守卫跳过
    assert (config.alpha_src / "AlphaWbaiArch").exists()


# ---------------------------------------------------------------------------
# approve
# ---------------------------------------------------------------------------

def test_approve_correlation_rejected(test_config, seed_factor):
    from ops.services.approve.approve import run_approve
    cfg_path, config = test_config
    seed_factor("AlphaWbaiApp", FactorStatus.REJECTED, last_fail_stage="correlation")
    run_approve(_args(cfg_path, factor_name="AlphaWbaiApp"))
    rec = _store(config).get("AlphaWbaiApp")
    assert rec.status == FactorStatus.ACTIVE
    assert rec.last_fail_stage is None
    # 留痕 approved
    assert rec.check_history[-1].fail_reason == "approved"


def test_approve_non_correlation_rejected(test_config, seed_factor):
    from ops.services.approve.approve import run_approve
    cfg_path, config = test_config
    seed_factor("AlphaWbaiApp2", FactorStatus.REJECTED, last_fail_stage="checkbias")
    run_approve(_args(cfg_path, factor_name="AlphaWbaiApp2"))
    # 非 correlation 失败 → 不放行
    assert _store(config).get("AlphaWbaiApp2").status == FactorStatus.REJECTED


def test_approve_active_rejected(test_config, seed_factor):
    from ops.services.approve.approve import run_approve
    cfg_path, config = test_config
    seed_factor("AlphaWbaiApp3", FactorStatus.ACTIVE)
    run_approve(_args(cfg_path, factor_name="AlphaWbaiApp3"))
    # 非 REJECTED → 报错不动
    assert _store(config).get("AlphaWbaiApp3").status == FactorStatus.ACTIVE


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------

def test_clear_orphan(test_config):
    from ops.services.clear.clear import run_clear
    cfg_path, config = test_config
    # staging 目录但 state 无 record → 孤儿
    (config.staging / "AlphaWbaiOrph").mkdir(parents=True, exist_ok=True)
    run_clear(_args(cfg_path, factor_name="AlphaWbaiOrph"))
    assert not (config.staging / "AlphaWbaiOrph").exists()


def test_clear_refuses_when_state_exists(test_config, seed_factor):
    from ops.services.clear.clear import run_clear
    cfg_path, config = test_config
    (config.staging / "AlphaWbaiHasRec").mkdir(parents=True, exist_ok=True)
    seed_factor("AlphaWbaiHasRec", FactorStatus.SUBMITTED)
    run_clear(_args(cfg_path, factor_name="AlphaWbaiHasRec"))
    # 有 state record → clear 拒绝,目录保留
    assert (config.staging / "AlphaWbaiHasRec").exists()


# ---------------------------------------------------------------------------
# rm
# ---------------------------------------------------------------------------

def test_rm_hard_deletes_all(test_config, seed_factor):
    from ops.services.rm.rm import run_rm
    cfg_path, config = test_config
    name = "AlphaWbaiRm"
    # 造全部落点
    (config.alpha_src / name).mkdir(parents=True, exist_ok=True)
    (config.alpha_src / name / f"{name}.py").write_text("x=1")
    (config.alpha_pnl / name).write_text("pnl")
    (config.alpha_dump / name).mkdir(parents=True, exist_ok=True)
    (config.alpha_feature / f"{name}.v1.npy").write_bytes(b"x")
    # bcorr 分流池副本(to_lib 写入;rm 不清则永远留在对比池,生产验证 L3-7 实测)
    (config.pnl_manual / name).write_text("pool-copy")
    (config.pnl_automated / name).write_text("pool-copy")
    # 在途副本(restage/overwrite 召回场景;rm 不清则 check 扫 staging 会复活因子)
    (config.staging / name).mkdir(parents=True, exist_ok=True)
    seed_factor(name, FactorStatus.ACTIVE)
    from ops.infra.snapshot import FactorSnapshot
    _snapshot(config).insert(FactorSnapshot(name=name, ret=1.0, shrp=1.0, mdd=1.0,
                                            tvr=1.0, fitness=1.0,
                                            snapshot_at="2026-07-05T00:00:00"))

    run_rm(_args(cfg_path, factor_name=name))

    # 全部落点清空
    assert not (config.alpha_src / name).exists()
    assert not (config.alpha_pnl / name).exists()
    assert not (config.alpha_dump / name).exists()
    assert not (config.alpha_feature / f"{name}.v1.npy").exists()
    assert not (config.pnl_manual / name).exists()      # 池副本一并清
    assert not (config.pnl_automated / name).exists()
    assert not (config.staging / name).exists()          # 在途副本一并清(防 check 复活)
    assert _store(config).get(name) is None
    assert _snapshot(config).get(name) is None  # info 级联删 snapshot


def test_rm_missing_factor(test_config):
    from ops.services.rm.rm import run_rm
    cfg_path, config = test_config
    # 不在 state → 报错返回,不炸
    run_rm(_args(cfg_path, factor_name="AlphaWbaiNope"))
    assert _store(config).get("AlphaWbaiNope") is None


# ---------------------------------------------------------------------------
# 批量 -u 路径 (风险最高: 一条命令动一片因子。单因子与批量是两套独立校验分支)
# ---------------------------------------------------------------------------

def test_approve_batch_only_correlation(test_config, seed_factor):
    """approve -u: 只放行 correlation-rejected,其他失败阶段归 skipped 不误放。"""
    from ops.services.approve.approve import run_approve
    cfg_path, config = test_config
    s = _store(config)
    seed_factor("AlphaWbaiC1", FactorStatus.REJECTED, last_fail_stage="correlation")
    seed_factor("AlphaWbaiC2", FactorStatus.REJECTED, last_fail_stage="checkbias")
    seed_factor("AlphaWbaiC3", FactorStatus.ACTIVE)
    run_approve(_args(cfg_path, user="wbai"))
    # 只有 correlation-rejected 被放行
    assert s.get("AlphaWbaiC1").status == FactorStatus.ACTIVE
    assert s.get("AlphaWbaiC2").status == FactorStatus.REJECTED  # checkbias 不放
    assert s.get("AlphaWbaiC3").status == FactorStatus.ACTIVE    # 本就 active,不动


def test_cancel_batch_only_eligible(test_config, seed_factor):
    """cancel -u: 只删 SUBMITTED,ACTIVE/REJECTED 归 skipped 不误删。"""
    from ops.services.cancel.cancel import run_cancel
    cfg_path, config = test_config
    for n in ("AlphaWbaiS1", "AlphaWbaiS2"):
        (config.staging / n).mkdir(parents=True, exist_ok=True)
    seed_factor("AlphaWbaiS1", FactorStatus.SUBMITTED)
    seed_factor("AlphaWbaiS2", FactorStatus.ACTIVE)
    run_cancel(_args(cfg_path, user="wbai", force=False))
    # SUBMITTED 删,ACTIVE 保留
    assert _store(config).get("AlphaWbaiS1") is None
    assert _store(config).get("AlphaWbaiS2").status == FactorStatus.ACTIVE


def test_cancel_batch_does_not_touch_other_user(test_config, seed_factor):
    """cancel -u wbai 不该动 mhe 的因子(author 从 factor_info 读)。"""
    from ops.services.cancel.cancel import run_cancel
    cfg_path, config = test_config
    (config.staging / "AlphaWbaiMine").mkdir(parents=True, exist_ok=True)
    (config.staging / "AlphaMheOther").mkdir(parents=True, exist_ok=True)
    seed_factor("AlphaWbaiMine", FactorStatus.SUBMITTED)
    seed_factor("AlphaMheOther", FactorStatus.SUBMITTED, author="mhe")
    run_cancel(_args(cfg_path, user="wbai", force=False))
    assert _store(config).get("AlphaWbaiMine") is None
    assert _store(config).get("AlphaMheOther") is not None  # 别人的不动

