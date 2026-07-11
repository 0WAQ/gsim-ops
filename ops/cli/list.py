import argparse

from ops.cli.common import METRIC_SORT_KEYS, STATUS_CHOICES, add_config_arg
from ops.services.list import run_list
from ops.utils.utils import LowerAction


def add_list_subparser(subparser: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparser.add_parser(
        "list",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="List factors in the library",
        epilog="""\
Example:
    ops list              # List all factors
    ops list -u wbai      # List factors by author
    ops list --sort-by shrp  # Sort by Sharpe ratio
    ops list --format json
""",
    )

    parser.add_argument(
        "--user",
        "-u",
        type=str,
        action=LowerAction,
        help="Filter by author (e.g., wbai)",
    )
    parser.add_argument(
        "--status",
        "-s",
        default=None,
        type=str,
        choices=list(STATUS_CHOICES),
        help="Filter by lifecycle status (e.g., active, rejected)",
    )
    parser.add_argument(
        "--format",
        "-f",
        type=str,
        default="table",
        choices=["table", "json"],
        help="Output format (default: table)",
    )
    # --refresh 已删除 (2026-07-07 Wave 2): list 改纯 PG 查询,不再有扫盘索引缓存。
    parser.add_argument(
        "--show-tables",
        action="store_true",
        help="Show tables column in output",
    )
    parser.add_argument(
        "--show-fields",
        action="store_true",
        help="Show fields column in output",
    )
    parser.add_argument(
        "--filter-by",
        type=str,
        help="Filter conditions separated by commas (e.g., tables=ashareeodprices,ret>30,shrp>1.5)",
    )
    # choices 从 metric 注册表派生(SSOT S8,core/metrics.SNAPSHOT_METRICS):
    # 原先手抄键列表,曾多一个 "delay",接受后被服务层静默忽略。delay 若要
    # 可排序,在注册表加一行(snapshot 表须有对应列)。
    parser.add_argument(
        "--sort-by",
        type=str,
        choices=list(METRIC_SORT_KEYS),
        help="Sort by field (descending)",
    )
    parser.add_argument(
        "-n",
        type=int,
        default=None,
        help="Limit output to first N factors",
    )
    add_config_arg(parser)

    parser.set_defaults(func=run_list)
