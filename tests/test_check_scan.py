"""扫描 / 自愈 / 锁循环测试 (PG)。

覆盖:_scan_factors 过滤、_ensure_record 补建、crash 自愈 (CHECKING 重跑)、
run_one 的 FactorLocked → 'locked' 分支。
"""
import queue

import pytest

from ops.core.state import FactorStatus

pytestmark = pytest.mark.pg


def _pipeline(config_path, checkers=None):
    from ops.services.check.check import CheckerPipeline
    return CheckerPipeline(users=None, config_path=config_path, checkers=checkers)


def _store(config):
    from ops.infra.store import default_store
    return default_store(config)


# ---------------------------------------------------------------------------
# _scan_factors 过滤
# ---------------------------------------------------------------------------

def test_scan_skips_invalid_dirs(test_config, make_factor):
    cfg_path, config = test_config
    make_factor(name="AlphaGood")
    # 缺 meta.json 的因子目录
    bad = config.staging / "AlphaNoMeta"
    bad.mkdir(parents=True, exist_ok=True)
    (bad / "AlphaNoMeta.py").write_text("x")
    (bad / "Config.AlphaNoMeta.xml").write_text("<gsim></gsim>")
    # 非 Alpha 前缀目录
    (config.staging / "randomdir").mkdir(parents=True, exist_ok=True)
    # meta.json 损坏
    broken = config.staging / "AlphaBroken"
    broken.mkdir(parents=True, exist_ok=True)
    (broken / "meta.json").write_text("{not json")

    pipe = _pipeline(cfg_path)
    names = {md.name for md in pipe.metadatas}
    assert names == {"AlphaGood"}


def test_scan_filters_by_user(test_config, make_factor):
    from ops.services.check.check import CheckerPipeline
    cfg_path, config = test_config
    make_factor(name="AlphaWbaiOne", author="wbai", submitted_by="wbai")
    make_factor(name="AlphaMheOne", author="mhe", submitted_by="mhe")
    pipe = CheckerPipeline(users=["wbai"], config_path=cfg_path, checkers={})
    assert {md.name for md in pipe.metadatas} == {"AlphaWbaiOne"}


def test_scan_filters_by_factor_name(test_config, make_factor):
    from ops.services.check.check import CheckerPipeline
    cfg_path, config = test_config
    make_factor(name="AlphaWbaiA")
    make_factor(name="AlphaWbaiB")
    pipe = CheckerPipeline(users=None, config_path=cfg_path, factor="AlphaWbaiA", checkers={})
    assert {md.name for md in pipe.metadatas} == {"AlphaWbaiA"}


# ---------------------------------------------------------------------------
# _ensure_record 补建 / 不覆盖
# ---------------------------------------------------------------------------

def test_ensure_record_creates_submitted(test_config, make_factor):
    # _ensure_record 阶段 2 起收 FactorRepository(register 原子写 info+state;
    # 原收裸 store —— 本用例曾滞后传 _store() 在 160 撞 AttributeError,
    # VERIFY-AGGREGATE-P2P3 阶段 1)
    from ops.infra.repository import FactorRepository

    cfg_path, config = test_config
    make_factor(name="AlphaEnsure", author="wbai")
    pipe = _pipeline(cfg_path, checkers={})
    factor = pipe.metadatas[0]
    store = _store(config)
    assert store.get("AlphaEnsure") is None
    pipe._ensure_record(factor, FactorRepository(config))
    rec = store.get("AlphaEnsure")
    assert rec is not None
    assert rec.status == FactorStatus.SUBMITTED
    # author 在 factor_info(FactorRecord 已无该字段)
    from ops.infra.info import default_info_store
    info = default_info_store(config).get("AlphaEnsure")
    assert info is not None and info.author == "wbai"


def test_ensure_record_does_not_overwrite(test_config, make_factor, seed_factor):
    from ops.infra.repository import FactorRepository

    cfg_path, config = test_config
    make_factor(name="AlphaExists", author="wbai")
    pipe = _pipeline(cfg_path, checkers={})
    factor = pipe.metadatas[0]
    store = _store(config)
    # 预置一个 ACTIVE record
    seed_factor("AlphaExists", FactorStatus.ACTIVE)
    pipe._ensure_record(factor, FactorRepository(config))
    # 不被覆盖
    assert store.get("AlphaExists").status == FactorStatus.ACTIVE


# ---------------------------------------------------------------------------
# crash 自愈:CHECKING 残留可被重跑覆盖
# ---------------------------------------------------------------------------

def test_checking_residue_reruns(test_config, make_factor, fake_checkers,
                                 fake_metrics, seed_factor):
    cfg_path, config = test_config
    make_factor(name="AlphaResidue")
    # 预置 CHECKING (模拟上次崩在半路)
    seed_factor("AlphaResidue", FactorStatus.CHECKING)
    # 预造 pass 路径产物
    (config.alpha_path / "AlphaResidue").mkdir(parents=True, exist_ok=True)
    (config.pnl_path / "AlphaResidue").write_text("pnl")
    checkers, _ = fake_checkers(fail_stage=None)
    pipe = _pipeline(cfg_path, checkers)
    ret = pipe.run_one(pipe.metadatas[0], 0, queue.Queue())
    # CHECKING 残留被正常走完 → ACTIVE
    assert ret == "pass"
    assert _store(config).get("AlphaResidue").status == FactorStatus.ACTIVE


# ---------------------------------------------------------------------------
# run_one 的 FactorLocked → 'locked'
# ---------------------------------------------------------------------------

def test_factor_locked_returns_locked(test_config, make_factor, fake_checkers):
    from ops.infra.lock import factor_lock

    cfg_path, config = test_config
    make_factor(name="AlphaLocked")
    checkers, _ = fake_checkers(fail_stage=None)
    pipe = _pipeline(cfg_path, checkers)
    factor = pipe.metadatas[0]

    # 先在另一处持有该因子的 advisory lock
    with factor_lock("AlphaLocked", config):
        ret = pipe.run_one(factor, 0, queue.Queue())
    assert ret == "locked"
    # state 未被改动 (仍无 record,因为根本没进 _run_one_locked)
    assert _store(config).get("AlphaLocked") is None
