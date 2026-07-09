"""check 流水线路由的无 PG 行为测试(json state 后端)。

`test_check_routing.py`(PG 版)覆盖全部 6 个结局分支但依赖 ops_test 库,
I2 基建重建前一直 skip —— 这里把 **5 个非 pass 结局**钉进 CI:json 后端 +
tmp 隔离的 fcntl 锁即可驱动(pass 结局的 archive 阶段要写 PG snapshot store,
仍归 PG 组)。同时验证 Wave 4 的两个行为点:

- **stage 归因由流水线盖章**:fake checker 抛不带 stage 的 CheckFail/CheckSkip,
  断言 last_fail_stage / failed_stage 落的是"当时正在跑的 stage";
- **prepare 失败响亮化**:stage prepare 落盘失败不再被吞,走 unexpected 臂
  revert SUBMITTED(原先静默拿错误窗口继续跑)。

_ensure_record 在 record 缺失时会去写 PG info store,故每个用例先 seed 一条
SUBMITTED record(与真实 submit 之后的状态一致)。
"""
import queue

import pytest

from ops.core.state import FactorRecord, FactorStatus
from ops.infra.store import default_store


def _seed(store, name: str) -> None:
    store.put(FactorRecord(name=name, status=FactorStatus.SUBMITTED, version=1,
                           updated_at="2026-07-05T00:00:00",
                           submitted_at="2026-07-05T00:00:00"))


def _run(cfg_path, config, name, checkers):
    from ops.services.check.check import CheckerPipeline
    store = default_store(config)
    _seed(store, name)
    pipe = CheckerPipeline(users=None, config_path=cfg_path,
                           factor=name, checkers=checkers)
    assert len(pipe.metadatas) == 1
    ret = pipe.run_one(pipe.metadatas[0], 0, queue.Queue())
    return ret, store.get(name)


def test_reject_early_stage(json_config, write_factor, fake_checkers):
    """checkbias 失败 → REJECTED + src 归档 + dump 清 + @module 重写。"""
    cfg_path, config = json_config
    write_factor(config, name="AlphaWbaiRejE")
    (config.alpha_dump / "AlphaWbaiRejE").mkdir(parents=True, exist_ok=True)
    checkers, _ = fake_checkers(fail_stage="checkbias", behavior="fail")

    ret, rec = _run(cfg_path, config, "AlphaWbaiRejE", checkers)
    assert ret == "fail"
    assert rec.status == FactorStatus.REJECTED
    # 归因是流水线盖的章(exception 不携带 stage)
    assert rec.last_fail_stage == "checkbias"
    assert rec.check_history[-1].failed_stage == "checkbias"
    assert (config.alpha_src / "AlphaWbaiRejE").exists()
    assert not (config.staging / "AlphaWbaiRejE").exists()
    assert not (config.alpha_dump / "AlphaWbaiRejE").exists()  # early → dump 清
    # rewrite_module_path 生效:@module 指向入库后的 .py
    xml = (config.alpha_src / "AlphaWbaiRejE" / "Config.AlphaWbaiRejE.xml").read_text()
    assert str(config.alpha_src / "AlphaWbaiRejE" / "AlphaWbaiRejE.py") in xml


def test_reject_late_stage_keeps_pnl(json_config, write_factor, fake_checkers):
    """compliance 失败(keep_artifacts_on_fail)→ REJECTED 但保留 pnl。"""
    cfg_path, config = json_config
    write_factor(config, name="AlphaWbaiRejL")
    (config.pnl_path / "AlphaWbaiRejL").write_text("pnl")
    (config.alpha_path / "AlphaWbaiRejL").mkdir(parents=True, exist_ok=True)
    checkers, _ = fake_checkers(fail_stage="compliance", behavior="fail")

    ret, rec = _run(cfg_path, config, "AlphaWbaiRejL", checkers)
    assert ret == "fail"
    assert rec.status == FactorStatus.REJECTED
    assert rec.last_fail_stage == "compliance"
    assert (config.alpha_pnl / "AlphaWbaiRejL").exists()


@pytest.mark.parametrize("stage", ["validate", "long_backtest"])
def test_retryable_reverts_to_submitted(json_config, write_factor, fake_checkers, stage):
    cfg_path, config = json_config
    write_factor(config, name="AlphaWbaiRetry")
    checkers, _ = fake_checkers(fail_stage=stage, behavior="fail")

    ret, rec = _run(cfg_path, config, "AlphaWbaiRetry", checkers)
    assert ret == "error"
    assert rec.status == FactorStatus.SUBMITTED
    assert rec.check_history[-1].failed_stage == stage
    assert rec.check_history[-1].passed is False
    assert (config.staging / "AlphaWbaiRetry").exists()
    assert not (config.alpha_src / "AlphaWbaiRetry").exists()


def test_skip_reverts_to_submitted(json_config, write_factor, fake_checkers):
    cfg_path, config = json_config
    write_factor(config, name="AlphaWbaiSkip")
    checkers, call_log = fake_checkers(fail_stage="checkpoint", behavior="skip")

    ret, rec = _run(cfg_path, config, "AlphaWbaiSkip", checkers)
    assert ret == "error"
    assert rec.status == FactorStatus.SUBMITTED
    assert rec.check_history[-1].passed is None
    assert rec.check_history[-1].failed_stage == "checkpoint"
    # short-circuit:checkpoint 之后的 checker 不被调
    assert call_log == ["validate", "checkbias", "checkpoint"]


def test_crash_reverts_to_submitted(json_config, write_factor, fake_checkers):
    cfg_path, config = json_config
    write_factor(config, name="AlphaWbaiCrash")
    checkers, call_log = fake_checkers(fail_stage="correlation", behavior="crash")

    ret, rec = _run(cfg_path, config, "AlphaWbaiCrash", checkers)
    assert ret == "error"
    assert rec.status == FactorStatus.SUBMITTED
    assert "unexpected" in (rec.check_history[-1].fail_reason or "")
    # crash 在最后一个 stage,前面全部跑到
    from ops.services.check.stages import STAGES
    assert call_log == list(STAGES)


def test_prepare_failure_is_loud(json_config, write_factor, fake_checkers, monkeypatch):
    """Wave 4 行为变更:stage prepare 落盘失败(如 JFS 写错误)不再被吞。

    原先 prepare_* 整段 try/except 吞掉,stage 拿着上个 stage 的窗口继续跑
    (validate 可能跑成全历史、checkbias 检查错误区间);现在异常直接抛,
    走 unexpected 臂 revert SUBMITTED,checker 一次都不该被调到。
    """
    import ops.services.check.xml_prepare as xp

    cfg_path, config = json_config
    write_factor(config, name="AlphaWbaiPrep")
    checkers, call_log = fake_checkers(fail_stage=None)
    store = default_store(config)
    _seed(store, "AlphaWbaiPrep")

    from ops.services.check.check import CheckerPipeline
    pipe = CheckerPipeline(users=None, config_path=cfg_path,
                           factor="AlphaWbaiPrep", checkers=checkers)

    # 构造后再 patch:prepare_for_initial(合法路径)已用真 save_xml 写过
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(xp, "save_xml", boom)

    ret = pipe.run_one(pipe.metadatas[0], 0, queue.Queue())
    rec = store.get("AlphaWbaiPrep")
    assert ret == "error"
    assert rec.status == FactorStatus.SUBMITTED
    assert "unexpected" in (rec.check_history[-1].fail_reason or "")
    assert call_log == []  # checker 未被调到
    assert (config.staging / "AlphaWbaiPrep").exists()
