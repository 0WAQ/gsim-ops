import argparse
import sys

from ops.cli.approve import add_approve_subparser
from ops.cli.backfill import add_backfill_subparser
from ops.cli.cancel import add_cancel_subparser
from ops.cli.check import add_check_subparser
from ops.cli.clear import add_clear_subparser
from ops.cli.combo import add_combo_subparser
from ops.cli.info import add_info_subparser
from ops.cli.list import add_list_subparser
from ops.cli.pack import add_pack_subparser
from ops.cli.restage import add_restage_subparser
from ops.cli.rm import add_rm_subparser
from ops.cli.run import add_run_subparser
from ops.cli.status import add_status_subparser
from ops.cli.submit import add_submit_subparser
from ops.infra.sudo import maybe_elevate
from ops.utils.log import logger


def main():
    try:
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
        add_submit_subparser(subparsers)
        add_status_subparser(subparsers)
        add_backfill_subparser(subparsers)
        add_pack_subparser(subparsers)
        add_rm_subparser(subparsers)
        add_restage_subparser(subparsers)
        add_approve_subparser(subparsers)
        add_cancel_subparser(subparsers)
        add_clear_subparser(subparsers)
        add_combo_subparser(subparsers)

        args = parser.parse_args()
        # JFS 集中运维: write 命令 + alpha_src root-owned 时自动 sudo 提权,
        # 否则 no-op (read-only 命令直通)。详见 ops/infra/sudo.py。
        # (ensure_redis_password 钩子随 redis state 后端一并退役, Wave 1 F2。)
        maybe_elevate(args)
        args.func(args)
    except SystemExit:
        raise  # argparse --help / explicit sys.exit() are control flow, not bugs
    except BaseException:
        logger.exception("ops crashed argv={}", sys.argv)
        raise
    finally:
        logger.complete()  # drain enqueue=True queue before exit


if __name__ == "__main__":
    main()
