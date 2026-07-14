import argparse

from ops.cli.common import add_config_arg, mark_write
from ops.services.approve import run_approve
from ops.utils.utils import LowerAction


def add_approve_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "approve",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="多样性豁免:放行 correlation-rejected 因子(REJECTED → ACTIVE)",
        epilog="""\
Example:
    ops approve AlphaWbaiFoo          # 单因子,询问确认
    ops approve -u wbai               # 批量:wbai 所有 correlation-rejected 因子
    ops approve -u wbai -y            # 批量,跳过确认
""",
    )

    parser.add_argument("factor_name", nargs="?", default=None, type=str,
                        help="单因子名;省略时配合 -u 批量")
    parser.add_argument("--user", "-u", dest="user", default=None,
                        type=str, action=LowerAction,
                        help="按 author 过滤(批量)")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="跳过交互确认")
    add_config_arg(parser)

    mark_write(parser)

    parser.set_defaults(func=run_approve)
