import argparse

from ops.cli.common import STATUS_CHOICES, add_config_arg
from ops.services.status import run_status
from ops.utils.utils import LowerAction


def add_status_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "status",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Example:
    ops status                       # all factors
    ops status AlphaWbaiReversal     # one factor (with check history)
    ops status -u wbai               # filter by author
    ops status -s/--status active    # filter by lifecycle status
""",
    )

    parser.add_argument("name", nargs="?", default=None, type=str, help="factor name (omit to list all)")
    parser.add_argument("--user", "-u", dest="author", default=None, type=str, action=LowerAction)
    parser.add_argument("--status", "-s", default=None, type=str, choices=list(STATUS_CHOICES))
    add_config_arg(parser)

    parser.set_defaults(func=run_status)
