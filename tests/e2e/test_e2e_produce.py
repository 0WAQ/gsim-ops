"""produce v3 端到端:真 gsim + 真 cc_2025,验"归档即生产态 → 薄驱动续跑"闭环。

窗口钉死 20251201..20251224(conftest 的 e2e produce 块):完全在 cc_2025
.meta 可见范围内,不依赖日历 TODAY —— 确定性。产线三根隔离在 tmp,
不触任何生产路径。

因子经真实 repo.archive 入库(生产化在归档内联发生),produce 直接跑
alpha_src 归档 XML 本尊;第二遍 = checkpoint 续跑,dump 集合不变(幂等)。
"""
from types import SimpleNamespace

import numpy as np
import pytest

from ops.core.dumpfiles import dump_dates, iter_dump_files
from ops.core.state import FactorRecord, FactorStatus
from ops.core.universe import load_universe

pytestmark = [pytest.mark.slow, pytest.mark.e2e]

_NAME = "AlphaWbaiProdE2E"
_START, _END = 20251201, 20251224


@pytest.fixture
def archived_active_factor(e2e_env, make_e2e_factor):
    """good 模板因子 → 真 repo.archive(归档生产化)→ PG 种 ACTIVE。"""
    from ops.infra.info import FactorInfo, default_info_store
    from ops.infra.repository import FactorRepository
    from ops.infra.store import default_store

    cfg_path, config, _ = e2e_env
    # 产线三根在生产上是既存 dataset;e2e 的 tmp 根自己建
    for d in (config.produce_dump_root, config.produce_pnl_root,
              config.produce_checkpoint_root):
        d.mkdir(parents=True, exist_ok=True)
    make_e2e_factor("good", _NAME)
    src = config.dropbox_path / "wbai" / "20260705" / _NAME
    dump = config.alpha_path / _NAME
    dump.mkdir(parents=True, exist_ok=True)
    pnl = config.pnl_path / _NAME
    pnl.write_text("placeholder")

    default_info_store(config).upsert(FactorInfo(
        name=_NAME, author="wbai", discovery_method="manual",
        created_at="2026-07-16T00:00:00"))
    FactorRepository(config).archive(_NAME, src_dir=src, dump_dir=dump,
                                     pnl_file=pnl, discovery_method="manual")
    default_store(config).put(FactorRecord(
        name=_NAME, status=FactorStatus.ACTIVE,
        updated_at="2026-07-16T00:00:00", submitted_at="2026-07-16T00:00:00",
        entered_at="2026-07-16T00:00:00"))
    return cfg_path, config


def test_e2e_produce_checkpoint_line(archived_active_factor):
    from ops.services.produce.produce import run_produce

    cfg_path, config = archived_active_factor
    dates, _, _ = load_universe(config.produce_nio_data_path)
    expected = {int(d) for d in dates if _START <= int(d) <= _END}
    assert expected, "cc_2025 在 202512 应有交易日"

    args = SimpleNamespace(factors=[_NAME], user=None, dry_run=False,
                           sync_only=False, force=False, enddate=None,
                           yes=True, workers=1, config_path=cfg_path)
    run_produce(args)

    dump_dir = config.produce_dump_root / _NAME
    got = dump_dates(dump_dir, require_both=True)
    assert got == expected                       # 窗口内交易日齐全(v1∧v2)
    arr = np.load(next(f for _, _, f in iter_dump_files(dump_dir)))
    assert arr.ndim == 1 and arr.size > 0        # 一维持仓向量
    assert (config.produce_pnl_root / _NAME).exists()          # pnl 同产(D6)

    # 第二遍 = checkpoint 续跑:dump 集合不变(尾部重写属预期,不比 mtime)
    run_produce(args)
    assert dump_dates(dump_dir, require_both=True) == expected
