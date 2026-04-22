import argparse
from pathlib import Path

from ops.utils.utils import LowerAction
from ops.infra.config import get_default_config_path
from ops.services.health import run_health


def add_health_subparser(subparser: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparser.add_parser(
        "health",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="Check factor library health",
        epilog="""\
Example:
    ops health                  # Report all issues
    ops health --fix            # Auto-refresh missing metrics/datasources
    ops health -u wbai          # Only check factors by author
""",
    )

    parser.add_argument("--user", "-u", type=str, action=LowerAction,
                        help="Filter by author")
    parser.add_argument("--fix", action="store_true",
                        help="Auto-refresh missing metrics/datasources")
    parser.add_argument("--refresh", "-r", action="store_true",
                        help="Force refresh index cache")
    parser.add_argument("--config-path", "-c", type=Path,
                        default=get_default_config_path())

    parser.set_defaults(func=run_health)
