import argparse
from .compiler import add_compiler_subparser
from .scp import add_scp_subparser
from .cp import add_cp_subparser
from .check import add_check_subparser
from .list import add_list_subparser
from .info import add_info_subparser


def main():
    parser = argparse.ArgumentParser(
        prog="ops",
        description="Gsim Operations Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(
        title="sub-command", dest="sub-command", required=True
    )
    # add_compiler_subparser(subparsers)
    # add_scp_subparser(subparsers)
    add_cp_subparser(subparsers)
    add_check_subparser(subparsers)
    add_list_subparser(subparsers)
    add_info_subparser(subparsers)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
