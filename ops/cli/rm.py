import argparse

from ops.cli.common import add_config_arg, mark_write
from ops.services.rm import run_rm


def add_rm_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "rm",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="彻底删除因子(不可逆)",
        epilog="""\
Example:
    ops rm AlphaWbaiFoo               # 彻底删除,交互确认
    ops rm AlphaWbaiFoo -y            # 跳过确认
""",
    )

    parser.add_argument("factor_name", type=str, help="Factor name to delete")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="Skip confirmation prompt")
    add_config_arg(parser)

    mark_write(parser)

    parser.set_defaults(func=run_rm)
