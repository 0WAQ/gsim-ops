import argparse
from pathlib import Path
from datetime import datetime

from ops.utils.utils import LowerAction
from ops.infra.config import get_default_config_path
from ops.services.check import run_check


def add_check_subparser(subparser: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparser.add_parser(
        "check",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Example:
    ops check -u wbai -s 20260101 -e 20260101
""",
    )

    today = datetime.today()
    parser.add_argument("--user", "-u", required=True, type=str, action=LowerAction)
    parser.add_argument("--start-date", "-s", default=today.strftime("%Y%m%d"))
    parser.add_argument("--end-date", "-e", default=today.strftime("%Y%m%d"))
    parser.add_argument("--factor-name", "-f", type=str, default=None)
    parser.add_argument(
        "--config-path", "-c", type=Path, default=get_default_config_path()
    )

    parser.set_defaults(func=run_check)
