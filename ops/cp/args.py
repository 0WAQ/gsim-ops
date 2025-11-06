import argparse
from .transfer import run_cp


def add_cp_subparser(subparser: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparser.add_parser(
        "cp",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Usage:
    ops cp -u wbai -s 20251030 -e 20251030"""
    )

    parser.add_argument("-u", "--unix-id", required=True)
    parser.add_argument("-s", "--start-date", required=True)
    parser.add_argument("-e", "--end-date", default=None)

    parser.set_defaults(func=run_cp)
