"""pipeline 路由测试 (主体, PG)。

依赖注入 fake checker,直接同进程调 run_one/_run_one_locked (绕开 ProcessPoolExecutor),
断言 5 个结局分支 (pass/retry/reject/skip/crash) 的 state 转移 + 文件落点 + check_history。
"""
import queue

import pytest

from ops.core.state import FactorStatus

pytestmark = pytest.mark.pg


def _pipeline(config_path, checkers):
    from ops.services.check.check import CheckerPipeline
    return CheckerPipeline(users=None, config_path=config_path, checkers=checkers)


def _store(config):
    from ops.infra.store import default_store
    return default_store(config)


def _derived(config):
    from ops.infra.derived import default_derived_store
    return default_derived_store(config)


def _prep_pass_artifacts(config, name):
    """pass 路径的 to_lib 会 move factor.alpha_dir 和 factor.pnl_file —— 预造它们。"""
    (config.alpha_path / name).mkdir(parents=True, exist_ok=True)
    (config.alpha_path / name / "dummy.npy").write_bytes(b"x")
    (config.pnl_path / name).write_text("pnl-data")


# ---------------------------------------------------------------------------
# pass 路径
# ---------------------------------------------------------------------------

def test_pass_archives_to_lib(test_config, make_factor, fake_checkers, fake_metrics):
    cfg_path, config = test_config
    make_factor(name="AlphaWbaiTest", discovery_method="manual")
    _prep_pass_artifacts(config, "AlphaWbaiTest")
    checkers, _ = fake_checkers(fail_stage=None)
    pipe = _pipeline(cfg_path, checkers)
    factor = pipe.metadatas[0]

    ret = pipe.run_one(factor, 0, queue.Queue())
    assert ret == "pass"

    # state → ACTIVE
    rec = _store(config).get("AlphaWbaiTest")
    assert rec.status == FactorStatus.ACTIVE
    assert rec.check_history[-1].passed is True

    # 文件落点:src/dump/pnl 进库,staging 清
    assert (config.alpha_src / "AlphaWbaiTest").exists()
    assert (config.alpha_dump / "AlphaWbaiTest").exists()
    assert (config.alpha_pnl / "AlphaWbaiTest").exists()
    assert not (config.staging / "AlphaWbaiTest").exists()

    # manual → pnl 分流到 pnl_manual
    assert (config.pnl_manual / "AlphaWbaiTest").exists()
    assert not (config.pnl_automated / "AlphaWbaiTest").exists()

    # derived 三组落库
    d = _derived(config).get("AlphaWbaiTest")
    assert d.ret == 15.0                      # fake_metrics
    assert d.fields == ["ashareeodprices.s_dq_close"]  # 真 AST parse
    # bcorr: fake corr_result 默认 None → 不写,这里断言 metrics+datasources 即可


def test_pass_automated_routes_pnl(test_config, make_factor, fake_checkers, fake_metrics):
    cfg_path, config = test_config
    make_factor(name="AlphaWbaiAuto", discovery_method="automated")
    _prep_pass_artifacts(config, "AlphaWbaiAuto")
    checkers, _ = fake_checkers(fail_stage=None)
    pipe = _pipeline(cfg_path, checkers)
    assert pipe.run_one(pipe.metadatas[0], 0, queue.Queue()) == "pass"
    assert (config.pnl_automated / "AlphaWbaiAuto").exists()
    assert not (config.pnl_manual / "AlphaWbaiAuto").exists()


def test_pass_missing_discovery_still_archives(test_config, make_factor, fake_checkers, fake_metrics):
    cfg_path, config = test_config
    make_factor(name="AlphaWbaiNoDm", discovery_method=None)
    _prep_pass_artifacts(config, "AlphaWbaiNoDm")
    checkers, _ = fake_checkers(fail_stage=None)
    pipe = _pipeline(cfg_path, checkers)
    assert pipe.run_one(pipe.metadatas[0], 0, queue.Queue()) == "pass"
    # 仍入库 (ACTIVE),只是不分流 pnl
    assert _store(config).get("AlphaWbaiNoDm").status == FactorStatus.ACTIVE
    assert not (config.pnl_manual / "AlphaWbaiNoDm").exists()
    assert not (config.pnl_automated / "AlphaWbaiNoDm").exists()


def test_pass_bcorr_persisted(test_config, make_factor, fake_checkers, fake_metrics):
    """correlation checker 返回带 max_bcorr 的 corr_result → bcorr 组落库。"""
    from ops.core.alpha.results.correlation import CorrResult
    from ops.core.metrics import Metrics

    cfg_path, config = test_config
    make_factor(name="AlphaWbaiBcorr")
    _prep_pass_artifacts(config, "AlphaWbaiBcorr")
    corr = CorrResult(metrics=Metrics(ret=15.0, tvr=40.0, shrp=2.5, mdd=8.0, fitness=1.2),
                      max_bcorr=0.42, max_bcorr_factor="AlphaOther")
    checkers, _ = fake_checkers(fail_stage=None, corr_result=corr)
    pipe = _pipeline(cfg_path, checkers)
    assert pipe.run_one(pipe.metadatas[0], 0, queue.Queue()) == "pass"
    d = _derived(config).get("AlphaWbaiBcorr")
    assert d.max_bcorr == 0.42
    assert d.max_bcorr_factor == "AlphaOther"


# ---------------------------------------------------------------------------
# retry 路径 (validate / long_backtest → SUBMITTED, 留 staging)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stage", ["validate", "long_backtest"])
def test_retry_reverts_to_submitted(test_config, make_factor, fake_checkers, stage):
    cfg_path, config = test_config
    make_factor(name="AlphaWbaiRetry")
    checkers, _ = fake_checkers(fail_stage=stage, behavior="fail")
    pipe = _pipeline(cfg_path, checkers)
    ret = pipe.run_one(pipe.metadatas[0], 0, queue.Queue())
    assert ret == "error"

    rec = _store(config).get("AlphaWbaiRetry")
    assert rec.status == FactorStatus.SUBMITTED
    assert rec.check_history[-1].passed is False
    assert rec.check_history[-1].failed_stage == stage
    # 留 staging,未进 alpha_src
    assert (config.staging / "AlphaWbaiRetry").exists()
    assert not (config.alpha_src / "AlphaWbaiRetry").exists()


# ---------------------------------------------------------------------------
# reject 路径 (其余 stage → REJECTED, src 进 alpha_src)
# ---------------------------------------------------------------------------

def test_reject_late_stage_keeps_pnl_dump(test_config, make_factor, fake_checkers):
    """compliance/correlation 失败:src 进库 + 保留 pnl/dump。"""
    cfg_path, config = test_config
    make_factor(name="AlphaWbaiRejLate")
    # 预造 pnl + dump (late stage 会尝试保留)
    (config.pnl_path / "AlphaWbaiRejLate").write_text("pnl")
    (config.alpha_path / "AlphaWbaiRejLate").mkdir(parents=True, exist_ok=True)
    checkers, _ = fake_checkers(fail_stage="compliance", behavior="fail")
    pipe = _pipeline(cfg_path, checkers)
    ret = pipe.run_one(pipe.metadatas[0], 0, queue.Queue())
    assert ret == "fail"

    rec = _store(config).get("AlphaWbaiRejLate")
    assert rec.status == FactorStatus.REJECTED
    assert rec.last_fail_stage == "compliance"
    assert (config.alpha_src / "AlphaWbaiRejLate").exists()
    assert not (config.staging / "AlphaWbaiRejLate").exists()
    # 保留 pnl
    assert (config.alpha_pnl / "AlphaWbaiRejLate").exists()


def test_reject_early_stage_wipes_dump(test_config, make_factor, fake_checkers):
    """checkbias/checkpoint 失败:src 进库 + 清 dump/feature。"""
    cfg_path, config = test_config
    make_factor(name="AlphaWbaiRejEarly")
    # 预造一个 dump 目录 (early stage 应清掉)
    (config.alpha_dump / "AlphaWbaiRejEarly").mkdir(parents=True, exist_ok=True)
    checkers, _ = fake_checkers(fail_stage="checkbias", behavior="fail")
    pipe = _pipeline(cfg_path, checkers)
    ret = pipe.run_one(pipe.metadatas[0], 0, queue.Queue())
    assert ret == "fail"

    rec = _store(config).get("AlphaWbaiRejEarly")
    assert rec.status == FactorStatus.REJECTED
    assert rec.last_fail_stage == "checkbias"
    assert (config.alpha_src / "AlphaWbaiRejEarly").exists()
    # dump 被清
    assert not (config.alpha_dump / "AlphaWbaiRejEarly").exists()


# ---------------------------------------------------------------------------
# skip 路径 (CheckSkip → SUBMITTED)
# ---------------------------------------------------------------------------

def test_skip_reverts_to_submitted(test_config, make_factor, fake_checkers):
    cfg_path, config = test_config
    make_factor(name="AlphaWbaiSkip")
    checkers, _ = fake_checkers(fail_stage="checkbias", behavior="skip")
    pipe = _pipeline(cfg_path, checkers)
    ret = pipe.run_one(pipe.metadatas[0], 0, queue.Queue())
    assert ret == "error"
    rec = _store(config).get("AlphaWbaiSkip")
    assert rec.status == FactorStatus.SUBMITTED
    assert rec.check_history[-1].passed is None  # skip → passed=None
    assert (config.staging / "AlphaWbaiSkip").exists()


# ---------------------------------------------------------------------------
# crash 路径 (普通 Exception → SUBMITTED, "unexpected:")
# ---------------------------------------------------------------------------

def test_crash_reverts_to_submitted(test_config, make_factor, fake_checkers):
    cfg_path, config = test_config
    make_factor(name="AlphaWbaiCrash")
    checkers, _ = fake_checkers(fail_stage="checkpoint", behavior="crash")
    pipe = _pipeline(cfg_path, checkers)
    ret = pipe.run_one(pipe.metadatas[0], 0, queue.Queue())
    assert ret == "error"
    rec = _store(config).get("AlphaWbaiCrash")
    assert rec.status == FactorStatus.SUBMITTED
    assert rec.check_history[-1].passed is None
    assert "unexpected" in (rec.check_history[-1].fail_reason or "")
    assert (config.staging / "AlphaWbaiCrash").exists()


# ---------------------------------------------------------------------------
# stage short-circuit:失败 stage 之后的 checker 不被调用
# ---------------------------------------------------------------------------

def test_stage_short_circuit(test_config, make_factor, fake_checkers):
    cfg_path, config = test_config
    make_factor(name="AlphaWbaiSC")
    checkers, call_log = fake_checkers(fail_stage="checkpoint", behavior="fail")
    pipe = _pipeline(cfg_path, checkers)
    pipe.run_one(pipe.metadatas[0], 0, queue.Queue())
    # checkpoint 之后 (long_backtest/compliance/correlation) 不应被调
    assert call_log == ["validate", "checkbias", "checkpoint"]


# ---------------------------------------------------------------------------
# _persist_derived 局部失败不阻断入库
# ---------------------------------------------------------------------------

def test_persist_derived_partial_failure_still_archives(
        test_config, make_factor, fake_checkers, fake_metrics, monkeypatch):
    from ops.core.alpha.results.correlation import CorrResult
    from ops.core.metrics import Metrics

    cfg_path, config = test_config
    make_factor(name="AlphaWbaiPartial")
    _prep_pass_artifacts(config, "AlphaWbaiPartial")
    corr = CorrResult(metrics=Metrics(ret=15.0, tvr=40.0, shrp=2.5, mdd=8.0, fitness=1.2),
                      max_bcorr=0.42, max_bcorr_factor="X")
    checkers, _ = fake_checkers(fail_stage=None, corr_result=corr)
    pipe = _pipeline(cfg_path, checkers)

    # 让 upsert_bcorr 抛异常 (模拟派生库局部失败)
    import ops.infra.derived.pg_store as pg
    orig = pg.PostgresDerivedStore.upsert_bcorr
    def boom(self, *a, **k):
        raise RuntimeError("bcorr write boom")
    monkeypatch.setattr(pg.PostgresDerivedStore, "upsert_bcorr", boom)

    ret = pipe.run_one(pipe.metadatas[0], 0, queue.Queue())
    # 仍入库
    assert ret == "pass"
    assert _store(config).get("AlphaWbaiPartial").status == FactorStatus.ACTIVE
    # metrics/datasources 已落 (bcorr 失败不影响它们)
    d = _derived(config).get("AlphaWbaiPartial")
    assert d.ret == 15.0
    assert d.fields == ["ashareeodprices.s_dq_close"]
    assert d.max_bcorr is None  # bcorr 写失败
