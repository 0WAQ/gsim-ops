import argparse
from pathlib import Path
from .list import run_list
from ..common.utils import LowerAction


def add_list_subparser(subparser: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparser.add_parser(
        "list",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="List factors in the library",
        epilog="""\
Example:
    ops list              # List all factors
    ops list -u wbai      # List factors by author
    ops list --format json
""")
    
    parser.add_argument("--user", "-u", type=str, action=LowerAction,
                        help="Filter by author (e.g., wbai)")
    parser.add_argument("--format", "-f", type=str, default="table",
                        choices=["table", "json"],
                        help="Output format (default: table)")
    parser.add_argument("--config-path", "-c", type=Path, 
                        default='/home/wbai/gsim-ops/config.yaml')
    
    parser.set_defaults(func=run_list)
