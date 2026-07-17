import argparse

from ops.cli.common import add_config_arg, mark_write
from ops.services.produce import run_produce


def add_produce_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "produce",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="在库因子 alpha_dump 日增生产(无状态,幂等)",
        epilog="""\
Example:
    ops produce                          # 全部 ACTIVE 因子,补到最新就绪日
    ops produce AlphaXxx AlphaYyy        # 指定因子
    ops produce -u wbai                  # 按作者过滤
    ops produce --date 20260716          # 显式目标日(须为就绪交易日)
    ops produce --dry-run                # 只列缺失区间,不跑
    ops produce --force --date 20260716 [--start 20260701] [-y]   # 重产(覆盖已有,确认制)
""",
    )

    parser.add_argument("factors", nargs="*", help="显式因子名(缺省 = 全部 ACTIVE)")
    parser.add_argument("--user", "-u", type=str, default=None, help="按作者过滤(批量模式)")
    parser.add_argument("--date", type=str, default=None,
                        help="目标日 YYYYMMDD(缺省 = 最新就绪交易日)")
    parser.add_argument("--start", type=str, default=None,
                        help="重产区间起点 YYYYMMDD(仅与 --force 连用)")
    parser.add_argument("--force", action="store_true",
                        help="覆盖已有 dump 重产(须显式 --date 锚定作用域,确认制)")
    parser.add_argument("--dry-run", action="store_true", help="仅列出每因子缺失日期,不执行")
    parser.add_argument("-y", "--yes", action="store_true", help="跳过 --force 确认")
    parser.add_argument("--workers", "-w", type=int, default=8, help="并行进程数 (默认 8)")
    add_config_arg(parser)

    mark_write(parser)

    parser.set_defaults(func=run_produce)
