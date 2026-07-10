"""Show factor details."""
import argparse

from ops.cli.common import add_config_arg
from ops.services.info import run_info


def add_info_subparser(subparser: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparser.add_parser(
        "info",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="Show factor details",
        epilog="""\
Example:
    ops info AlphaWbaiMomentum
""",
    )

    parser.add_argument("factor_name", type=str, help="Factor name (e.g., AlphaWbaiMomentum)")
    add_config_arg(parser)

    parser.set_defaults(func=run_info)
