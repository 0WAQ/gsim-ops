import argparse
from .factor import run_compiler


def add_compiler_subparser(subparser: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparser.add_parser(
        "compiler",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    required_group = parser.add_argument_group("required options")
    required_group.add_argument("-d", "--date-dir", required=True, help="format: YYYYMMDD")
    required_group.add_argument("-u", "--unix-id", required=True)

    opt_group = parser.add_argument_group("optional options")
    opt_group.add_argument("--venv-path", default="/home/wbai/.venvs/gsim/")
    opt_group.add_argument("--compile-opt", default="-O2")

    flag_group = parser.add_argument_group("flag options")
    flag_group.add_argument("--xml-backup", action="store_true", default=False)
    
    parser.set_defaults(func=run_compiler)

