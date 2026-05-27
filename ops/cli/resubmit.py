import argparse
from pathlib import Path

from ops.utils.utils import LowerAction
from ops.core.state import FactorStatus
from ops.infra.config import get_default_config_path
from ops.services.resubmit import run_resubmit


def add_resubmit_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "resubmit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="将 ACTIVE 因子打回 staging 重新审查",
        epilog="""\
Example:
    ops resubmit AlphaWbaiFoo                  # 单因子,询问确认
    ops resubmit AlphaWbaiFoo -y               # 跳过确认
    ops resubmit AlphaWbaiFoo --purge          # 同时清除 dump + feature(pnl 保留)
    ops resubmit -u wbai                       # 批量:wbai 所有 active 因子,询问确认
    ops resubmit -u wbai -y                    # 批量,跳过确认

默认仅搬 alpha_src + 翻状态,alpha_dump / alpha_feature / alpha_pnl 保留。
状态变更会通过 ops sync push 的 state merge 传播到远端;远端 alpha_src 不会被动。
""",
    )

    parser.add_argument("factor_name", nargs="?", default=None, type=str,
                        help="单因子名;省略时配合 -u / -s 批量")
    parser.add_argument("--user", "-u", dest="user", default=None,
                        type=str, action=LowerAction,
                        help="按 author 过滤(批量)")
    parser.add_argument("--status", "-s", default=FactorStatus.ACTIVE.value,
                        choices=[FactorStatus.ACTIVE.value],
                        help="来源状态(目前仅支持 active)")
    parser.add_argument("--purge", action="store_true",
                        help="同步清除 alpha_dump + alpha_feature(alpha_pnl 保留)")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="跳过交互确认")
    parser.add_argument("--config-path", "-c", type=Path,
                        default=get_default_config_path())

    parser.set_defaults(func=run_resubmit)
