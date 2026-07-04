import argparse
from pathlib import Path

from ops.utils.utils import LowerAction
from ops.infra.config import get_default_config_path
from ops.services.refresh import run_refresh


def add_refresh_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "refresh",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="Recompute derived data (metrics/datasources/bcorr) and persist",
        epilog="""\
Example:
    ops refresh                    # 三组全刷 (全库)
    ops refresh --metrics          # 只刷 metrics
    ops refresh --datasources
    ops refresh --bcorr
    ops refresh -u wbai            # 限定作者
    ops refresh AlphaXxx           # 单因子

check 归档时这三组已自动落库;本命令用于运维/legacy 修复
(补 REJECTED 因子、backfill 旧因子、派生库重建)。
""",
    )

    parser.add_argument("name", nargs="?", default=None, type=str,
                        help="factor name (omit for whole library)")
    parser.add_argument("--user", "-u", default=None, type=str, action=LowerAction,
                        help="Filter by author (e.g., wbai)")
    parser.add_argument("--metrics", action="store_true",
                        help="Refresh metrics via simsummary")
    parser.add_argument("--datasources", action="store_true",
                        help="Refresh data sources by parsing factor code")
    parser.add_argument("--bcorr", action="store_true",
                        help="Refresh max bcorr by running bcorr")
    parser.add_argument("--config-path", "-c", type=Path,
                        default=get_default_config_path())

    parser.set_defaults(func=run_refresh)
