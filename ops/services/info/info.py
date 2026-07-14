"""info 查询编排 —— 零展示(Tree 渲染在 `ops/cli/info.py`,本模块只采集数据)。"""
from dataclasses import dataclass

from ops.core.factor import Factor
from ops.core.library import LibraryScanner, ScannedFactor
from ops.core.paths import FactorPaths
from ops.infra.config import Config
from ops.infra.repository import FactorRepository


@dataclass
class InfoData:
    """单因子全景 + 物理现场,渲染所需的全部数据。"""
    factor: Factor                                # identity / state / snapshot(PG)
    paths: FactorPaths                            # 盘面布局(src/dump/pnl 落点)
    physical: ScannedFactor | None                # 现场 stat;None = alpha_src 目录缺失(漂移)
    date_range: tuple[str | None, str | None]     # dump 首末日期


def collect_info(args) -> InfoData | None:
    """采集单因子详情。None = factor_info 无记录(存在性判据 = PG,三表的根 ——
    用"alpha_src 目录存在"判定会与 status/rm/cancel 的 state 判据不一致,
    同一因子可能 status 里存在、info 里 not found)。"""
    name = args.factor_name
    config = Config.load(args.config_path)

    repo = FactorRepository(config)
    factor = repo.get(name)
    if factor is None:
        return None

    # 物理状态:单因子现场 stat(便宜,只碰本因子路径)。scanner.get 返回 None
    # 表示 src 目录缺失(PG 有记录但盘上没有 —— 显示出来,让漂移可见)。
    scanner = LibraryScanner(config)
    physical = scanner.get(name)
    date_range = scanner.get_dump_date_range(name)

    return InfoData(factor=factor, paths=repo.paths(name),
                    physical=physical, date_range=date_range)
