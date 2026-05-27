import argparse
from pathlib import Path

from ops.infra.config import get_default_config_path
from ops.services.run.run import run_factors


def add_run_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "run",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Example:
    ops run -f AlphaWbaiFoo                        # run one factor (full history)
    ops run -f AlphaWbaiFoo -s 20250101 -e 20250131   # custom date range
    ops run -f AlphaWbaiFoo --pack                    # run + pack alpha_feature
    ops run -u wbai                                # run all factors by user
""",
    )

    parser.add_argument("--user", "-u", default=None, type=str, help="filter by submitted_by")
    parser.add_argument("--factor-name", "-f", type=str, default=None, help="run one factor by name")
    parser.add_argument("--start-date", "-s", type=str, default="20100101", help="backtest start date (default: 20100101)")
    parser.add_argument("--end-date", "-e", type=str, default="20251231", help="backtest end date (default: 20251231)")
    parser.add_argument("--pack", action="store_true", help="incremental pack after run")
    parser.add_argument("--config-path", "-c", type=Path, default=get_default_config_path())

    parser.set_defaults(func=run_factors)
