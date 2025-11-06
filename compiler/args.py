import argparse

from .factor import run_compiler

def add_compiler_subparser(subparser: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparser.add_parser(
        "compiler",
        help="",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    required_group = parser.add_argument_group("核心必填参数")
    required_group.add_argument("-d", "--date-dir", required=True, help="format: YYYYMMDD")
    # required_group.add_argument("-u", "--unix-id", required=True)

    opt_group = parser.add_argument_group("可选配置参数")
    opt_group.add_argument("--venv-path", default="/tmp/cython/.venv")
    opt_group.add_argument("--compile-opt", default="-O2")

    flag_group = parser.add_argument_group("选项配置参数")
    flag_group.add_argument("--xml-backup", action="store_true", default=False)
    
    parser.set_defaults(func=run_compiler)

