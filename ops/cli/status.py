import argparse

from ops.utils.utils import LowerAction
from ops.core.state import FactorStatus
from ops.services.status import run_status


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
    parser.add_argument("--status", "-s", default=None, type=str, choices=[s.value for s in FactorStatus])

    parser.set_defaults(func=run_status)
