import argparse
from pathlib import Path

from ops.infra.config import get_default_config_path
from ops.services.backfill import run_backfill


def add_backfill_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "backfill",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Example:
    ops backfill --dry-run     # 预览
    ops backfill               # 执行
""",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="只扫描不写入")
    parser.add_argument(
        "--config-path", "-c", type=Path, default=get_default_config_path()
    )
    parser.set_defaults(func=run_backfill)
