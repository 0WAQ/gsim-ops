from ops.core.library import LibraryScanner
from ops.infra.config import Config
from ops.services.list.metrics import refresh_metrics
from ops.services.list.datasource import refresh_datasources
from ops.services.list.bcorr import refresh_bcorr
from ops.utils.printer import banner, info, bottom


def _resolve_names(args, config: Config) -> list[str]:
    """单因子(positional name) > 作者过滤 > 全库。names 由 alpha_src 索引得出。"""
    if args.name:
        return [args.name]
    scanner = LibraryScanner.from_config_path(args.config_path)
    factors = scanner.scan()
    if args.user:
        factors = scanner.filter_by_author(factors, args.user)
    return [f.name for f in factors]


def run_refresh(args):
    """手动重算派生数据 (metrics/datasources/bcorr) 并落库。

    check 归档时这三组已自动落库 (见 services/check);本命令是运维/legacy 修复
    入口 —— 补 REJECTED 因子、backfill 的旧因子、或派生库异常时的重建。
    无 --metrics/--datasources/--bcorr 任一 flag 时默认三组全刷。
    """
    config = Config.load(args.config_path)

    do_metrics = args.metrics
    do_datasources = args.datasources
    do_bcorr = args.bcorr
    if not (do_metrics or do_datasources or do_bcorr):
        do_metrics = do_datasources = do_bcorr = True

    names = _resolve_names(args, config)

    banner("刷新派生数据")
    if not names:
        info("没有匹配的因子")
        bottom()
        return

    scope = args.name or (f"author={args.user}" if args.user else "全库")
    info(f"目标 : {len(names)} 个因子 ({scope})")

    if do_metrics:
        refresh_metrics(names, config, args.config_path)
        info("✔ metrics 已刷新")
    if do_datasources:
        refresh_datasources(names, config, args.config_path)
        info("✔ datasources 已刷新")
    if do_bcorr:
        refresh_bcorr(names, config, args.config_path)
        info("✔ bcorr 已刷新")

    bottom()
