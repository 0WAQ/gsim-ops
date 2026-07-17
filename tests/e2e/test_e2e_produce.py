"""produce 端到端:真实 gsim + 真实 cc 数据,验日增 dump 生产闭环。

妙处:不需要 cc_all —— produce 根也指 cc_2025(.meta 锁 20251231),
production_start 落在 2025-12 内,窗口 20251201..20251224 完全在快照可见
范围里。160/170 均可跑;cc_all 上的黄金对比(与 check 产 dump 逐日比对)
是 170 实机验证项,不在测试套件里。

因子不走 submit/check(那是 pipeline e2e 的领域):直接手放 alpha_src +
种 PG ACTIVE —— produce 只消费"在库"这个事实,不关心入库路径。
"""
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from ops.core.dumpfiles import dump_dates, iter_dump_files
from ops.core.state import FactorRecord, FactorStatus
from ops.core.universe import load_universe

pytestmark = [pytest.mark.slow, pytest.mark.e2e]

_NAME = "AlphaWbaiProdE2E"
_START, _TARGET = 20251201, 20251224


@pytest.fixture
def produce_e2e(e2e_env, make_e2e_factor):
    """e2e config 加 produce 块(根 = cc_2025)+ alpha_src 里种一个 ACTIVE 因子。"""
    import shutil

    from ops.infra.info import FactorInfo, default_info_store
    from ops.infra.store import default_store

    cfg_path, config, _ = e2e_env
    raw = yaml.safe_load(cfg_path.read_text())
    nio = raw["path"]["nio_data_path"]                    # = 真实 cc_2025
    ws = config.alpha_src.parent / "produce_ws"
    raw["produce"] = {"nio_data_path": nio, "production_start": str(_START),
                      "workspace": str(ws)}
    cfg_path.write_text(yaml.safe_dump(raw, allow_unicode=True))

    make_e2e_factor("good", _NAME)                        # 写进隔离 dropbox
    src = config.dropbox_path / "wbai" / "20260705" / _NAME
    shutil.copytree(src, config.alpha_src / _NAME)

    default_info_store(config).upsert(FactorInfo(
        name=_NAME, author="wbai", discovery_method="manual",
        created_at="2026-07-16T00:00:00"))
    default_store(config).put(FactorRecord(
        name=_NAME, status=FactorStatus.ACTIVE,
        updated_at="2026-07-16T00:00:00", submitted_at="2026-07-16T00:00:00",
        entered_at="2026-07-16T00:00:00"))

    from ops.infra.config import Config
    return cfg_path, Config.load(cfg_path)


def test_e2e_produce_fills_window_and_is_idempotent(produce_e2e):
    from ops.services.produce.produce import run_produce

    cfg_path, config = produce_e2e
    dates, _, _ = load_universe(config.produce_nio_data_path)
    expected = {int(d) for d in dates if _START <= int(d) <= _TARGET}
    assert expected, "cc_2025 在 202512 应有交易日"

    args = SimpleNamespace(factors=[_NAME], user=None, date=str(_TARGET),
                           start=None, force=False, dry_run=False, yes=True,
                           workers=1, config_path=cfg_path)
    run_produce(args)

    dump_dir = config.alpha_dump / _NAME
    assert dump_dates(dump_dir, require_both=True) == expected
    # 每份 dump 是 (H0,) 一维持仓向量
    for _date, _v, f in iter_dump_files(dump_dir):
        arr = np.load(f)
        assert arr.ndim == 1 and arr.size > 0
        break

    # 幂等:第二遍全部"已最新",文件分毫不动
    sample = next(f for _, _, f in iter_dump_files(dump_dir))
    mtime = sample.stat().st_mtime_ns
    run_produce(args)
    assert sample.stat().st_mtime_ns == mtime
