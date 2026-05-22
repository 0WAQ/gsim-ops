import argparse

from ops.cli.check import add_check_subparser
from ops.cli.list import add_list_subparser
from ops.cli.info import add_info_subparser
from ops.cli.health import add_health_subparser
from ops.cli.submit import add_submit_subparser
from ops.cli.status import add_status_subparser
from ops.cli.backfill import add_backfill_subparser
from ops.cli.pack import add_pack_subparser
from ops.cli.sync import add_sync_subparser


def main():
    parser = argparse.ArgumentParser(
        prog="ops",
        description="Gsim Operations Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(
        title="sub-command", dest="sub-command", required=True
    )

    add_check_subparser(subparsers)
    add_list_subparser(subparsers)
    add_info_subparser(subparsers)
    add_health_subparser(subparsers)
    add_submit_subparser(subparsers)
    add_status_subparser(subparsers)
    add_backfill_subparser(subparsers)
    add_pack_subparser(subparsers)
    add_sync_subparser(subparsers)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
