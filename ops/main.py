import argparse
import os
import sys

from ops.cli.approve import add_approve_subparser
from ops.cli.cancel import add_cancel_subparser
from ops.cli.check import add_check_subparser
from ops.cli.clear import add_clear_subparser
from ops.cli.combo import add_combo_subparser
from ops.cli.doctor import add_doctor_subparser
from ops.cli.info import add_info_subparser
from ops.cli.list import add_list_subparser
from ops.cli.pack import add_pack_subparser
from ops.cli.restage import add_restage_subparser
from ops.cli.rm import add_rm_subparser
from ops.cli.run import add_run_subparser
from ops.cli.setup import add_setup_subparser
from ops.cli.status import add_status_subparser
from ops.cli.submit import add_submit_subparser
from ops.infra.sudo import maybe_elevate
from ops.utils.log import logger

# 子命令注册表 —— main 与测试共用的单一正主(否则声明集测试另抄一份注册
# 函数列表,新命令不会自动进测试,又是一面会漂的镜子)。新增子命令 =
# 在此加一行;写共享盘的命令还须在其注册函数里 mark_write(cli/common)。
SUBPARSER_REGISTRARS = (
    add_run_subparser,
    add_check_subparser,
    add_list_subparser,
    add_info_subparser,
    add_submit_subparser,
    add_status_subparser,
    add_pack_subparser,
    add_rm_subparser,
    add_restage_subparser,
    add_approve_subparser,
    add_cancel_subparser,
    add_clear_subparser,
    add_combo_subparser,
    add_setup_subparser,
    add_doctor_subparser,
)


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

        for add_subparser in SUBPARSER_REGISTRARS:
            add_subparser(subparsers)

        args = parser.parse_args()
        # JFS 集中运维: write 命令 + alpha_src root-owned 时自动 sudo 提权,
        # 否则 no-op (read-only 命令直通)。详见 ops/infra/sudo.py。
        maybe_elevate(args)
        args.func(args)
    except SystemExit:
        raise  # argparse --help / explicit sys.exit() are control flow, not bugs
    except BrokenPipeError:
        # 下游管道提前关闭(`ops list | head` / `| less` 退出)是正常 Unix 行为,
        # 不是崩溃。把 stdout 换成 /dev/null 防解释器退出时二次 flush 再炸,
        # 按管道约定以 141 (128+SIGPIPE) 退出,不打 traceback。
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        sys.exit(141)
    except BaseException:
        logger.exception("ops crashed argv={}", sys.argv)
        raise
    finally:
        logger.complete()  # drain enqueue=True queue before exit


if __name__ == "__main__":
    main()
