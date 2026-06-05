import argparse

from ops.cli.run import add_run_subparser
from ops.cli.check import add_check_subparser
from ops.cli.list import add_list_subparser
from ops.cli.info import add_info_subparser
from ops.cli.health import add_health_subparser
from ops.cli.submit import add_submit_subparser
from ops.cli.status import add_status_subparser
from ops.cli.backfill import add_backfill_subparser
from ops.cli.pack import add_pack_subparser
from ops.cli.sync import add_sync_subparser
from ops.cli.rm import add_rm_subparser
from ops.cli.resubmit import add_resubmit_subparser
from ops.cli.recheck import add_recheck_subparser
from ops.cli.approve import add_approve_subparser
from ops.infra.sudo import maybe_elevate


def main():
    parser = argparse.ArgumentParser(
        prog="ops",
        description="Gsim Operations Tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(
        title="sub-command", dest="sub-command", required=True
    )

    add_run_subparser(subparsers)
    add_check_subparser(subparsers)
    add_list_subparser(subparsers)
    add_info_subparser(subparsers)
    add_health_subparser(subparsers)
    add_submit_subparser(subparsers)
    add_status_subparser(subparsers)
    add_backfill_subparser(subparsers)
    add_pack_subparser(subparsers)
    add_sync_subparser(subparsers)
    add_rm_subparser(subparsers)
    add_resubmit_subparser(subparsers)
    add_recheck_subparser(subparsers)
    add_approve_subparser(subparsers)

    args = parser.parse_args()
    # JFS 集中运维: write 命令 + alpha_src root-owned 时自动 sudo 提权,
    # 否则 no-op (legacy prod 模式 / read-only 命令直通)。详见 ops/infra/sudo.py。
    maybe_elevate(args)
    args.func(args)


if __name__ == "__main__":
    main()
