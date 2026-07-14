import argparse

from ops.cli.common import add_config_arg, mark_write
from ops.services.cancel import run_cancel
from ops.utils.utils import LowerAction


def add_cancel_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "cancel",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="撤回未入库的 submitted 因子",
        epilog="""\
Example:
    ops cancel AlphaWbaiFoo           # 单因子,询问确认
    ops cancel AlphaWbaiFoo --force   # 同时允许 CHECKING(清崩溃残留)
    ops cancel -u wbai                # 批量:wbai 所有 submitted 因子
""",
    )

    parser.add_argument("factor_name", nargs="?", default=None, type=str,
                        help="单因子名;省略时配合 -u 批量")
    parser.add_argument("--user", "-u", dest="user", default=None,
                        type=str, action=LowerAction,
                        help="按 author 过滤(批量)")
    parser.add_argument("--force", action="store_true",
                        help="同时允许 CHECKING(清崩溃 / 中断的 check 残留)")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="跳过交互确认")
    add_config_arg(parser)

    mark_write(parser)

    parser.set_defaults(func=run_cancel)
