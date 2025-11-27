import argparse
from .check_bias import run_check_bias, check_bias
from ..common.utils import LowerAction


def add_check_bias_subparser(subparser: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparser.add_parser(
        "check-bias",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Usage:
    ops check-bias -u wbai -s 20251030 -e 20251030"""
    )

    parser.add_argument("-u", "--unix-id", type=str, required=True, action=LowerAction)
    parser.add_argument("-s", "--start-date", type=str, required=True)
    parser.add_argument("-e", "--end-date", type=str, default=None)

    parser.add_argument("--dropbox-directory", type=str, default="/mnt/storage/dropbox")

    parser.set_defaults(func=check_bias)
