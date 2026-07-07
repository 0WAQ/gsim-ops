import argparse
from pathlib import Path

from ops.core.state import FactorStatus
from ops.infra.config import get_default_config_path
from ops.services.restage import run_restage
from ops.utils.utils import LowerAction


def add_restage_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "restage",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="把已入库因子召回 staging,等待重跑 check(原代码不变)",
        epilog="""\
Example:
    ops restage AlphaWbaiFoo                  # 单因子,询问确认
    ops restage AlphaWbaiFoo -y               # 跳过确认
    ops restage AlphaWbaiFoo --purge          # 同时清除 dump + feature(pnl 保留)
    ops restage -u wbai                       # 批量:wbai 所有 active 因子
    ops restage -u wbai -s rejected           # 批量:wbai 所有 rejected 因子

来源状态:
  active   ← alpha_src/<name>/
  rejected ← alpha_src/<name>/

默认仅搬源 + 翻状态,alpha_dump / alpha_feature / alpha_pnl 保留。
搬回 staging 后需 ops check 才真正重跑;version 不变。
""",
    )

    parser.add_argument("factor_name", nargs="?", default=None, type=str,
                        help="单因子名;省略时配合 -u / -s 批量")
    parser.add_argument("--user", "-u", dest="user", default=None,
                        type=str, action=LowerAction,
                        help="按 author 过滤(批量)")
    # 默认 None(而非 'active'):批量模式必须显式给 -u 和/或 -s 才会执行。
    # 若给默认值,服务层的"必须指定选择器"守卫永远不触发,裸 `ops restage -y`
    # 会把全库 ACTIVE 因子搬出 alpha_src(full-review 第一部分 1.2 高危项)。
    parser.add_argument("--status", "-s", default=None,
                        choices=[FactorStatus.ACTIVE.value,
                                 FactorStatus.REJECTED.value],
                        help="来源状态 (active/rejected;批量模式缺省按 active)")
    parser.add_argument("--purge", action="store_true",
                        help="同步清除 alpha_dump + alpha_feature(alpha_pnl 保留)")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="跳过交互确认")
    parser.add_argument("--config-path", "-c", type=Path,
                        default=get_default_config_path())

    parser.set_defaults(func=run_restage)
