"""Show factor details."""
import argparse
from pathlib import Path

from ops.infra.config import get_default_config_path
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
    parser.add_argument("--config-path", "-c", type=Path, default=get_default_config_path())

    parser.set_defaults(func=run_info)
