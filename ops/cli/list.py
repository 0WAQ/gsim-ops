import argparse
from pathlib import Path

from ops.utils.utils import LowerAction
from ops.infra.config import get_default_config_path
from ops.core.state import FactorStatus
from ops.services.list import run_list


def add_list_subparser(subparser: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparser.add_parser(
        "list",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="List factors in the library",
        epilog="""\
Example:
    ops list              # List all factors
    ops list -u wbai      # List factors by author
    ops list --refresh       # Force refresh index cache
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
        choices=[s.value for s in FactorStatus],
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
    parser.add_argument(
        "--refresh",
        "-r",
        action="store_true",
        help="Force refresh index cache",
    )
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
    # choices 与 list.py 的 _SORTABLE_KEYS 对齐:原先多一个 "delay",接受后
    # 被服务层静默忽略(full-review 第三部分 S8)。delay 若要可排序,须同时
    # 进 _SORTABLE_KEYS 与 snapshot _METRIC_EXPR。
    parser.add_argument(
        "--sort-by",
        type=str,
        choices=["ret", "shrp", "mdd", "tvr", "fitness", "bcorr"],
        help="Sort by field (descending)",
    )
    parser.add_argument(
        "-n",
        type=int,
        default=None,
        help="Limit output to first N factors",
    )
    parser.add_argument(
        "--config-path", "-c", type=Path, default=get_default_config_path()
    )

    parser.set_defaults(func=run_list)
