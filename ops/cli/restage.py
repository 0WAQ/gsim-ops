import argparse

from ops.cli.common import FactorStatus, add_config_arg, mark_write
from ops.services.restage import run_restage
from ops.utils.utils import LowerAction


def add_restage_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "restage",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="召回已入库因子到 staging 重跑 check(原代码不变)",
        epilog="""\
Example:
    ops restage AlphaWbaiFoo                 # 单因子,询问确认
    ops restage AlphaWbaiFoo --purge         # 同时清 dump + feature(pnl 保留)
    ops restage -u wbai -s rejected          # 批量:wbai 所有 rejected 因子
""",
    )

    parser.add_argument("factor_name", nargs="?", default=None, type=str,
                        help="单因子名;省略时配合 -u / -s 批量")
    parser.add_argument("--user", "-u", dest="user", default=None,
                        type=str, action=LowerAction,
                        help="按 author 过滤(批量)")
    # 默认 None(而非 'active'):批量模式必须显式给 -u 和/或 -s 才会执行。
    # 若给默认值,服务层的"必须指定选择器"守卫永远不触发,裸 `ops restage -y`
    # 会把全库 ACTIVE 因子搬出 alpha_src。
    parser.add_argument("--status", "-s", default=None,
                        choices=[FactorStatus.ACTIVE.value,
                                 FactorStatus.REJECTED.value],
                        help="来源状态 (active/rejected;批量模式缺省按 active)")
    parser.add_argument("--purge", action="store_true",
                        help="同步清除 alpha_dump + alpha_feature(alpha_pnl 保留)")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="跳过交互确认")
    add_config_arg(parser)

    mark_write(parser)

    parser.set_defaults(func=run_restage)
