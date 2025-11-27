import argparse
from .transfer import run_cp
from ..common.utils import LowerAction


def add_cp_subparser(subparser: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparser.add_parser(
        "cp",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Usage:
    ops cp -u wbai -s 20251030 -e 20251030"""
    )

    parser.add_argument("-u", "--unix-id", type=str, required=True, action=LowerAction)
    parser.add_argument("-s", "--start-date", type=str, required=True)
    parser.add_argument("-e", "--end-date", type=str, default=None)

    parser.add_argument("--venv-path", type=str, default="/home/wbai/.venvs/gsim/")
    parser.add_argument("--compile-opt", type=str, default="-O2")
    parser.add_argument("--xml-backup", action="store_true", default=False)     # TODO: is default useful?
    parser.add_argument("--enable-backtest", action="store_true", default=False) # TODO: is default useful?
    parser.add_argument("--dropbox-directory", type=str, default="/mnt/storage/dropbox")

    parser.set_defaults(func=run_cp)
