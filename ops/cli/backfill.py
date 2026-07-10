import argparse

from ops.cli.common import add_config_arg
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
    
    parser.add_argument("--dry-run", action="store_true", help="只扫描不写入")
    add_config_arg(parser)
    
    parser.set_defaults(func=run_backfill)
