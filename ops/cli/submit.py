import argparse
from datetime import datetime

from ops.cli.common import add_config_arg
from ops.services.submit import run_submit
from ops.utils.utils import LowerAction


def add_submit_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "submit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Example:
    ops submit -u wbai -s 20260101 -e 20260101
    ops submit -u wbai -s 20260101 -f AlphaWbaiXxx
    ops submit -u wbai -s 20260101 --overwrite   # 已入库因子改提新代码 (version+1)
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

    parser.set_defaults(func=run_submit)
