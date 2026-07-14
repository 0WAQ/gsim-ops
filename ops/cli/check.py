import argparse

from ops.cli.common import add_config_arg, mark_write
from ops.services.check import run_check
from ops.utils.utils import LowerAction


def add_check_subparser(subparser: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparser.add_parser(
        "check",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="对 staging 因子跑验证流水线",
        epilog="""\
Example:
    ops check                       # 检测 staging 全部因子
    ops check -u wbai               # 按作者过滤
    ops check -f AlphaWbaiReversal  # 检测单个因子
""",
    )

    parser.add_argument("--user", "-u", default=None, type=str, action=LowerAction, help="filter by submitted_by")
    parser.add_argument("--factor-name", "-f", type=str, default=None, help="check one factor by name")
    # --retry 已删除:解析后从未被读取(no-op)。retry 语义早已由自动路由取代 ——
    # validate/long_backtest 失败自动回 SUBMITTED 留在 staging,下次 ops check
    # 无条件重扫(full-review 第三部分 V 表)。
    add_config_arg(parser)

    mark_write(parser)

    parser.set_defaults(func=run_check)
