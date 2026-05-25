import argparse
from pathlib import Path

from ops.infra.config import get_default_config_path
from ops.services.rm import run_rm


def add_rm_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "rm",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="Soft-delete a factor (mark state=DELETED)",
        epilog="""\
Example:
    ops rm AlphaWbaiFoo                # 软删除:仅打标 DELETED,文件保留
    ops rm AlphaWbaiFoo --force        # 同时删本地 dump + feature(保留 src/pnl)
    ops rm AlphaWbaiFoo -y             # 跳过确认

list / health 默认隐藏 deleted 因子;`ops list -s deleted` 可查看。
状态会通过 `ops sync push` 的 state merge 同步到远端,远端文件不会被动。
""",
    )

    parser.add_argument("factor_name", type=str, help="Factor name to delete")
    parser.add_argument("--force", action="store_true",
                        help="Also remove local dump dir + feature .npy (src/pnl kept)")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="Skip confirmation prompt")
    parser.add_argument("--config-path", "-c", type=Path,
                        default=get_default_config_path())

    parser.set_defaults(func=run_rm)
