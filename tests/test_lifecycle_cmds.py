"""cancel / approve / clear / rm 写路径测试 (PG)。

- cancel: SUBMITTED (--force + CHECKING) 删 staging + 硬删 state;其他状态拒绝
- approve: 仅 correlation-rejected → ACTIVE;其他失败阶段 / 非 REJECTED 拒绝
- clear:  仅 staging 孤儿 (state 无 record);有 record 报错让用 cancel
- rm:     硬删全部落点 (src/pnl/dump/feature + state + derived 行)
"""
from types import SimpleNamespace

import pytest

from ops.core.state import FactorStatus, FactorRecord

pytestmark = pytest.mark.pg


def _store(config):
    from ops.infra.store import default_store
    return default_store(config)


def _derived(config):
    from ops.infra.derived import default_derived_store
    return default_derived_store(config)


def _args(cfg_path, **kw):
    d = dict(config_path=cfg_path, yes=True, user=None, factor_name=None)
    d.update(kw)
    return SimpleNamespace(**d)


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------

def test_cancel_submitted(test_config):
    from ops.services.cancel.cancel import run_cancel
    cfg_path, config = test_config
    (config.staging / "AlphaWbaiCan").mkdir(parents=True, exist_ok=True)
    _store(config).put(FactorRecord(name="AlphaWbaiCan", author="wbai",
                                    status=FactorStatus.SUBMITTED,
                                    updated_at="2026-07-05T00:00:00"))
    run_cancel(_args(cfg_path, factor_name="AlphaWbaiCan", force=False))
    # staging 删 + state 硬删
    assert not (config.staging / "AlphaWbaiCan").exists()
    assert _store(config).get("AlphaWbaiCan") is None


def test_cancel_checking_needs_force(test_config):
    from ops.services.cancel.cancel import run_cancel
    cfg_path, config = test_config
    (config.staging / "AlphaWbaiChk").mkdir(parents=True, exist_ok=True)
    _store(config).put(FactorRecord(name="AlphaWbaiChk", author="wbai",
                                    status=FactorStatus.CHECKING,
                                    updated_at="2026-07-05T00:00:00"))
    # 不带 --force → CHECKING 不被 cancel
    run_cancel(_args(cfg_path, factor_name="AlphaWbaiChk", force=False))
    assert _store(config).get("AlphaWbaiChk") is not None
    # 带 --force → 清掉
    run_cancel(_args(cfg_path, factor_name="AlphaWbaiChk", force=True))
    assert _store(config).get("AlphaWbaiChk") is None


def test_cancel_active_rejected(test_config):
    from ops.services.cancel.cancel import run_cancel
    cfg_path, config = test_config
    _store(config).put(FactorRecord(name="AlphaWbaiActC", author="wbai",
                                    status=FactorStatus.ACTIVE,
                                    updated_at="2026-07-05T00:00:00"))
    run_cancel(_args(cfg_path, factor_name="AlphaWbaiActC", force=False))
    # ACTIVE 不能 cancel → 保留
    assert _store(config).get("AlphaWbaiActC").status == FactorStatus.ACTIVE


# ---------------------------------------------------------------------------
# approve
# ---------------------------------------------------------------------------

def test_approve_correlation_rejected(test_config):
    from ops.services.approve.approve import run_approve
    cfg_path, config = test_config
    _store(config).put(FactorRecord(name="AlphaWbaiApp", author="wbai",
                                    status=FactorStatus.REJECTED,
                                    updated_at="2026-07-05T00:00:00",
                                    last_fail_stage="correlation"))
    run_approve(_args(cfg_path, factor_name="AlphaWbaiApp"))
    rec = _store(config).get("AlphaWbaiApp")
    assert rec.status == FactorStatus.ACTIVE
    assert rec.last_fail_stage is None
    # 留痕 approved
    assert rec.check_history[-1].fail_reason == "approved"


def test_approve_non_correlation_rejected(test_config):
    from ops.services.approve.approve import run_approve
    cfg_path, config = test_config
    _store(config).put(FactorRecord(name="AlphaWbaiApp2", author="wbai",
                                    status=FactorStatus.REJECTED,
                                    updated_at="2026-07-05T00:00:00",
                                    last_fail_stage="checkbias"))
    run_approve(_args(cfg_path, factor_name="AlphaWbaiApp2"))
    # 非 correlation 失败 → 不放行
    assert _store(config).get("AlphaWbaiApp2").status == FactorStatus.REJECTED


def test_approve_active_rejected(test_config):
    from ops.services.approve.approve import run_approve
    cfg_path, config = test_config
    _store(config).put(FactorRecord(name="AlphaWbaiApp3", author="wbai",
                                    status=FactorStatus.ACTIVE,
                                    updated_at="2026-07-05T00:00:00"))
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


def test_clear_refuses_when_state_exists(test_config):
    from ops.services.clear.clear import run_clear
    cfg_path, config = test_config
    (config.staging / "AlphaWbaiHasRec").mkdir(parents=True, exist_ok=True)
    _store(config).put(FactorRecord(name="AlphaWbaiHasRec", author="wbai",
                                    status=FactorStatus.SUBMITTED,
                                    updated_at="2026-07-05T00:00:00"))
    run_clear(_args(cfg_path, factor_name="AlphaWbaiHasRec"))
    # 有 state record → clear 拒绝,目录保留
    assert (config.staging / "AlphaWbaiHasRec").exists()


# ---------------------------------------------------------------------------
# rm
# ---------------------------------------------------------------------------

def test_rm_hard_deletes_all(test_config):
    from ops.services.rm.rm import run_rm
    cfg_path, config = test_config
    name = "AlphaWbaiRm"
    # 造全部落点
    (config.alpha_src / name).mkdir(parents=True, exist_ok=True)
    (config.alpha_src / name / f"{name}.py").write_text("x=1")
    (config.alpha_pnl / name).write_text("pnl")
    (config.alpha_dump / name).mkdir(parents=True, exist_ok=True)
    (config.alpha_feature / f"{name}.v1.npy").write_bytes(b"x")
    _store(config).put(FactorRecord(name=name, author="wbai", status=FactorStatus.ACTIVE,
                                    updated_at="2026-07-05T00:00:00"))
    _derived(config).upsert_metrics(name, {"ret": 1.0, "shrp": 1.0, "mdd": 1.0,
                                           "tvr": 1.0, "fitness": 1.0})

    run_rm(_args(cfg_path, factor_name=name))

    # 全部落点清空
    assert not (config.alpha_src / name).exists()
    assert not (config.alpha_pnl / name).exists()
    assert not (config.alpha_dump / name).exists()
    assert not (config.alpha_feature / f"{name}.v1.npy").exists()
    assert _store(config).get(name) is None
    assert _derived(config).get(name) is None


def test_rm_missing_factor(test_config):
    from ops.services.rm.rm import run_rm
    cfg_path, config = test_config
    # 不在 state → 报错返回,不炸
    run_rm(_args(cfg_path, factor_name="AlphaWbaiNope"))
    assert _store(config).get("AlphaWbaiNope") is None
