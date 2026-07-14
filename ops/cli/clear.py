import argparse

from ops.cli.common import add_config_arg, mark_write
from ops.services.clear import run_clear
from ops.utils.utils import LowerAction


def add_clear_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "clear",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="清理 staging 孤儿目录(state 无 record)",
        epilog="""\
Example:
    ops clear                    # 扫全部孤儿,询问确认
    ops clear AlphaLhwFoo        # 单孤儿
    ops clear -u lhw -y          # 仅 lhw 推断作者的孤儿,跳过确认
""",
    )

    parser.add_argument("factor_name", nargs="?", default=None, type=str,
                        help="单个孤儿目录名;省略时扫全部")
    parser.add_argument("--user", "-u", dest="user", default=None,
                        type=str, action=LowerAction,
                        help="按推断的 author 过滤(批量)")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="跳过交互确认")
    add_config_arg(parser)

    mark_write(parser)

    parser.set_defaults(func=run_clear)
