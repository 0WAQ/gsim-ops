import argparse

from ops.cli.common import add_config_arg, mark_write
from ops.services.run.run import run_factors


def add_run_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "run",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="在因子库中跑回测",
        epilog="""\
Example:
    ops run -f AlphaWbaiFoo                     # 单因子,全历史
    ops run -f AlphaWbaiFoo -s 20250101 -e 20250131   # 指定区间
    ops run -u wbai                             # 跑某作者全部因子
""",
    )

    parser.add_argument("--user", "-u", default=None, type=str, help="filter by submitted_by")
    parser.add_argument("--factor-name", "-f", type=str, default=None, help="run one factor by name")
    parser.add_argument("--start-date", "-s", type=str, default="20100101", help="backtest start date (default: 20100101)")
    parser.add_argument("--end-date", "-e", type=str, default="20251231", help="backtest end date (default: 20251231)")
    # --pack 已删除:epilog 宣传 "run + pack" 但服务层从未读取该 dest(no-op 谎言,
    # full-review 第三部分 V 表)。要打包用独立的 ops pack。
    add_config_arg(parser)

    mark_write(parser)

    parser.set_defaults(func=run_factors)
