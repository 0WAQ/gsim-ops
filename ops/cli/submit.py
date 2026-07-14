import argparse
from datetime import datetime

from ops.cli.common import add_config_arg, mark_write
from ops.services.submit import run_submit
from ops.utils.utils import LowerAction


def add_submit_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "submit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="从 dropbox 提交因子到 staging",
        epilog="""\
Example:
    ops submit -u wbai -s 20260101              # 某日全部因子
    ops submit -u wbai -s 20260101 -f AlphaWbaiXxx    # 单个因子
    ops submit -u wbai -s 20260101 --overwrite  # 已入库改提新代码 (version+1)
""",
    )

    today = datetime.today()
    parser.add_argument("--user", "-u", required=True, type=str, action=LowerAction)
    parser.add_argument("--start-date", "-s", default=today.strftime("%Y%m%d"))
    parser.add_argument("--end-date", "-e", default=None)
    parser.add_argument("--factor-name", "-f", type=str, default=None)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖已入库的同名因子,提交新代码并 version+1(默认跳过已入库因子)",
    )
    add_config_arg(parser)

    mark_write(parser)

    parser.set_defaults(func=run_submit)
