"""restage 写路径测试 (PG)。

覆盖 run_restage / _resolve_targets / _restage_one 分支:
- ACTIVE / REJECTED 召回 → SUBMITTED,src 搬回 staging
- REJECTED 自动清 dump/feature/pnl;ACTIVE 默认保留,--purge 才清
- 不支持的状态 / 源缺失 / staging 已存在拒绝覆盖
"""
import pytest

from ops.core.state import FactorStatus

pytestmark = pytest.mark.pg


def _store(config):
    from ops.infra.store import default_store
    return default_store(config)


def _seed_src(config, name):
    """在 alpha_src 造因子源目录(state/info 行由 seed_factor fixture 种)。"""
    src = config.alpha_src / name
    src.mkdir(parents=True, exist_ok=True)
    (src / f"{name}.py").write_text("x = 1\n")
    (src / f"Config.{name}.xml").write_text(
        '<gsim><Modules><Alpha id="M" module="old"></Alpha></Modules></gsim>')
    return src


def _args(cfg_path, **kw):
    from types import SimpleNamespace
    d = dict(config_path=cfg_path, yes=True, user=None, factor_name=None,
             status="active", purge=False)
    d.update(kw)
    return SimpleNamespace(**d)


def test_restage_active_moves_to_staging(test_config, seed_factor):
    from ops.services.restage.restage import run_restage
    cfg_path, config = test_config
    _seed_src(config, "AlphaWbaiAct")
    seed_factor("AlphaWbaiAct", FactorStatus.ACTIVE)
    run_restage(_args(cfg_path, factor_name="AlphaWbaiAct"))
    rec = _store(config).get("AlphaWbaiAct")
    assert rec.status == FactorStatus.SUBMITTED
    assert (config.staging / "AlphaWbaiAct").exists()
    assert not (config.alpha_src / "AlphaWbaiAct").exists()


def test_restage_active_keeps_artifacts(test_config, seed_factor):
    from ops.services.restage.restage import run_restage
    cfg_path, config = test_config
    _seed_src(config, "AlphaWbaiKeep")
    seed_factor("AlphaWbaiKeep", FactorStatus.ACTIVE)
    # 预造 dump + feature
    (config.alpha_dump / "AlphaWbaiKeep").mkdir(parents=True, exist_ok=True)
    (config.alpha_feature / "AlphaWbaiKeep.v1.npy").write_bytes(b"x")
    run_restage(_args(cfg_path, factor_name="AlphaWbaiKeep", purge=False))
    # 默认保留
    assert (config.alpha_dump / "AlphaWbaiKeep").exists()
    assert (config.alpha_feature / "AlphaWbaiKeep.v1.npy").exists()


def test_restage_active_purge_wipes_artifacts(test_config, seed_factor):
    from ops.services.restage.restage import run_restage
    cfg_path, config = test_config
    _seed_src(config, "AlphaWbaiPurge")
    seed_factor("AlphaWbaiPurge", FactorStatus.ACTIVE)
    (config.alpha_dump / "AlphaWbaiPurge").mkdir(parents=True, exist_ok=True)
    (config.alpha_feature / "AlphaWbaiPurge.v1.npy").write_bytes(b"x")
    (config.alpha_pnl / "AlphaWbaiPurge").write_text("pnl")
    run_restage(_args(cfg_path, factor_name="AlphaWbaiPurge", purge=True))
    # --purge 清 dump + feature;pnl 始终保留 (ACTIVE)
    assert not (config.alpha_dump / "AlphaWbaiPurge").exists()
    assert not (config.alpha_feature / "AlphaWbaiPurge.v1.npy").exists()
    assert (config.alpha_pnl / "AlphaWbaiPurge").exists()


def test_restage_rejected_wipes_pnl(test_config, seed_factor):
    from ops.services.restage.restage import run_restage
    cfg_path, config = test_config
    name = "AlphaWbaiRej"
    _seed_src(config, name)
    seed_factor(name, FactorStatus.REJECTED, last_fail_stage="correlation")
    (config.alpha_pnl / name).write_text("pnl")
    run_restage(_args(cfg_path, factor_name=name, status="rejected"))
    assert _store(config).get(name).status == FactorStatus.SUBMITTED
    # REJECTED restage 额外清 pnl
    assert not (config.alpha_pnl / name).exists()


def test_restage_unsupported_status_rejected(test_config, seed_factor):
    from ops.services.restage.restage import run_restage
    cfg_path, config = test_config
    seed_factor("AlphaWbaiSub", FactorStatus.SUBMITTED)
    run_restage(_args(cfg_path, factor_name="AlphaWbaiSub"))
    # SUBMITTED 不支持 restage → 状态不变
    assert _store(config).get("AlphaWbaiSub").status == FactorStatus.SUBMITTED


def test_restage_missing_source_skipped(test_config, seed_factor):
    from ops.services.restage.restage import run_restage
    cfg_path, config = test_config
    # state ACTIVE 但 alpha_src 无目录
    seed_factor("AlphaWbaiGone", FactorStatus.ACTIVE)
    run_restage(_args(cfg_path, factor_name="AlphaWbaiGone"))
    # 源缺失 → 不动状态
    assert _store(config).get("AlphaWbaiGone").status == FactorStatus.ACTIVE


def test_restage_batch_respects_author(test_config, seed_factor):
    """批量 -u 路径:只召回指定作者的因子,不误动其他作者(author 在 factor_info)。"""
    from ops.services.restage.restage import run_restage
    cfg_path, config = test_config
    # 混入多作者因子:wbai 2 个 ACTIVE,mhe 1 个 ACTIVE
    for n, a in (("AlphaWbaiA", "wbai"), ("AlphaWbaiB", "wbai"), ("AlphaMheX", "mhe")):
        _seed_src(config, n)
        seed_factor(n, FactorStatus.ACTIVE, author=a)

    # restage -u wbai
    run_restage(_args(cfg_path, user="wbai", status="active"))

    # wbai 的两个应被召回 → SUBMITTED + 进 staging
    assert _store(config).get("AlphaWbaiA").status == FactorStatus.SUBMITTED
    assert _store(config).get("AlphaWbaiB").status == FactorStatus.SUBMITTED
    assert (config.staging / "AlphaWbaiA").exists()
    assert (config.staging / "AlphaWbaiB").exists()

    # mhe 的不动:仍 ACTIVE,仍在 alpha_src
    rec_mhe = _store(config).get("AlphaMheX")
    assert rec_mhe.status == FactorStatus.ACTIVE
    assert (config.alpha_src / "AlphaMheX").exists()
    assert not (config.staging / "AlphaMheX").exists()

