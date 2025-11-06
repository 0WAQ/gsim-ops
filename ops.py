import argparse

from compiler import add_compiler_subparser
from scp_transfer import add_scp_subparser


def main():
    parser = argparse.ArgumentParser(
        prog="ops",
        description="GSIM ops",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Usage:
  1. 编译因子: ops compiler -d 20251031 -u wbai --xml-backup
  2. 本地 -> 远程传输: ops scp /local/path wbai@10.6.100.146:/remote/path -p password
  3. 远程 -> 本地传输: ops scp wbai@10.6.100.146:/remote/path /local/path -p password
    """)

    subparsers = parser.add_subparsers(title="sub tools", dest="subcommand", required=True)
    add_compiler_subparser(subparsers)
    add_scp_subparser(subparsers)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()