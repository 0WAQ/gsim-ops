"""submit 写路径测试 (PG)。

覆盖 run_submit / submit_one 的分支:
- 新因子 → SUBMITTED version=1
- 已入库默认跳过;--overwrite → version+1
- 文件数不合规 / syntax error / discovery_method 缺失 → fail (回滚 staging,不留 orphan)
- 从 dropbox 复制到 staging + 生成 meta.json
"""
import pytest

from ops.core.state import FactorStatus

pytestmark = pytest.mark.pg


def _store(config):
    from ops.infra.store import default_store
    return default_store(config)


def test_submit_new_factor(test_config, make_dropbox_factor, make_args):
    from ops.services.submit.submit import run_submit
    cfg_path, config = test_config
    make_dropbox_factor(name="AlphaWbaiNew", user="wbai", date="20260705")
    run_submit(make_args(user="wbai", start_date="20260705", end_date="20260705",
                         factor_name=None, overwrite=False))
    rec = _store(config).get("AlphaWbaiNew")
    assert rec is not None
    assert rec.status == FactorStatus.SUBMITTED
    assert rec.version == 1
    # staging 目录 + meta.json 就位
    assert (config.staging / "AlphaWbaiNew").exists()
    assert (config.staging / "AlphaWbaiNew" / "meta.json").exists()


def test_submit_existing_skipped_without_overwrite(test_config, make_dropbox_factor,
                                                   make_args, seed_factor):
    from ops.services.submit.submit import run_submit
    cfg_path, config = test_config
    # 预置已入库
    seed_factor("AlphaWbaiExist", FactorStatus.ACTIVE, version=3)
    make_dropbox_factor(name="AlphaWbaiExist", user="wbai", date="20260705")
    run_submit(make_args(user="wbai", start_date="20260705", end_date="20260705",
                         factor_name=None, overwrite=False))
    # 未被改动 (仍 ACTIVE version=3)
    rec = _store(config).get("AlphaWbaiExist")
    assert rec.status == FactorStatus.ACTIVE
    assert rec.version == 3


def test_submit_overwrite_bumps_version(test_config, make_dropbox_factor,
                                        make_args, seed_factor):
    from ops.services.submit.submit import run_submit
    cfg_path, config = test_config
    seed_factor("AlphaWbaiOw", FactorStatus.ACTIVE, version=2)
    # 旧版本的 check 面产物(--overwrite 应回收,防新代码重检撞旧版自鬼影,PV7)
    (config.alpha_pnl / "AlphaWbaiOw").write_text("old-pnl")
    (config.pnl_manual / "AlphaWbaiOw").write_text("old-pool")
    make_dropbox_factor(name="AlphaWbaiOw", user="wbai", date="20260705")
    run_submit(make_args(user="wbai", start_date="20260705", end_date="20260705",
                         factor_name=None, overwrite=True))
    rec = _store(config).get("AlphaWbaiOw")
    assert rec.status == FactorStatus.SUBMITTED
    assert rec.version == 3  # 2 + 1
    assert not (config.alpha_pnl / "AlphaWbaiOw").exists()
    assert not (config.pnl_manual / "AlphaWbaiOw").exists()


def test_submit_missing_discovery_fails(test_config, make_dropbox_factor, make_args):
    from ops.services.submit.submit import run_submit
    cfg_path, config = test_config
    make_dropbox_factor(name="AlphaWbaiNoDm", user="wbai", date="20260705",
                        discovery_method=None)
    run_submit(make_args(user="wbai", start_date="20260705", end_date="20260705",
                         factor_name=None, overwrite=False))
    # 硬校验失败:不入库,staging 回滚
    assert _store(config).get("AlphaWbaiNoDm") is None
    assert not (config.staging / "AlphaWbaiNoDm").exists()


def test_submit_bad_file_count_fails(test_config, make_dropbox_factor, make_args):
    from ops.services.submit.submit import run_submit
    cfg_path, config = test_config
    d = make_dropbox_factor(name="AlphaWbaiBad", user="wbai", date="20260705")
    # 多塞一个 .py → 文件数不合规
    (d / "extra.py").write_text("x = 1")
    run_submit(make_args(user="wbai", start_date="20260705", end_date="20260705",
                         factor_name=None, overwrite=False))
    assert _store(config).get("AlphaWbaiBad") is None
    assert not (config.staging / "AlphaWbaiBad").exists()


def test_submit_syntax_error_fails(test_config, make_dropbox_factor, make_args):
    from ops.services.submit.submit import run_submit
    cfg_path, config = test_config
    d = make_dropbox_factor(name="AlphaWbaiSyn", user="wbai", date="20260705")
    (d / "AlphaWbaiSyn.py").write_text("def broken(:\n  pass")  # syntax error
    run_submit(make_args(user="wbai", start_date="20260705", end_date="20260705",
                         factor_name=None, overwrite=False))
    assert _store(config).get("AlphaWbaiSyn") is None
    assert not (config.staging / "AlphaWbaiSyn").exists()


def test_submit_no_factors_found(test_config, make_args):
    from ops.services.submit.submit import run_submit
    cfg_path, config = test_config
    # dropbox 空 → 不报错,静默返回
    run_submit(make_args(user="wbai", start_date="20260705", end_date="20260705",
                         factor_name=None, overwrite=False))
    assert _store(config).list() == []


def test_submit_rejects_bogus_birthday(test_config, make_dropbox_factor, make_args):
    """L1(2026-07-12 TRIAGE):birthday 给了但离谱(如 zxu 的 20061219)拒收
    并回滚 staging;PG 零写入。"""
    from ops.services.submit.submit import run_submit
    cfg_path, config = test_config
    make_dropbox_factor(name="AlphaWbaiBadBday", user="wbai", date="20260705",
                        birthday=20061219)
    run_submit(make_args(user="wbai", start_date="20260705", end_date="20260705",
                         factor_name=None, overwrite=False))
    assert not (config.staging / "AlphaWbaiBadBday").exists()   # 回滚
    assert _store(config).get("AlphaWbaiBadBday") is None       # PG 零写入
