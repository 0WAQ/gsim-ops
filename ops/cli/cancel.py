import argparse
from pathlib import Path

from ops.infra.config import get_default_config_path
from ops.services.cancel import run_cancel
from ops.utils.utils import LowerAction


def add_cancel_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "cancel",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="撤回未入库的因子(staging 里的 submitted)",
        epilog="""\
Example:
    ops cancel AlphaWbaiFoo            # 单因子,询问确认
    ops cancel AlphaWbaiFoo -y         # 跳过确认
    ops cancel AlphaWbaiFoo --force    # 同时允许 CHECKING(清崩溃残留)
    ops cancel -u wbai                 # 批量:wbai 所有 submitted 因子
    ops cancel -u wbai -y              # 批量,跳过确认

适用状态: submitted(--force 也允许 checking)。
清理: 删 staging/<name>/ + 硬删 state record。
因子从未 ACTIVE 过,不留 tombstone(区别于 ops rm)。
""",
    )

    parser.add_argument("factor_name", nargs="?", default=None, type=str,
                        help="单因子名;省略时配合 -u 批量")
    parser.add_argument("--user", "-u", dest="user", default=None,
                        type=str, action=LowerAction,
                        help="按 author 过滤(批量)")
    parser.add_argument("--force", action="store_true",
                        help="同时允许 CHECKING(清崩溃 / 中断的 check 残留)")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="跳过交互确认")
    parser.add_argument("--config-path", "-c", type=Path,
                        default=get_default_config_path())

    parser.set_defaults(func=run_cancel)
