import argparse
from pathlib import Path

from ops.infra.config import get_default_config_path
from ops.services.approve import run_approve
from ops.utils.utils import LowerAction


def add_approve_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "approve",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="人工豁免:为数据覆盖多样性放行 correlation-rejected 因子,REJECTED → ACTIVE",
        epilog="""\
Example:
    ops approve AlphaWbaiFoo          # 单因子,询问确认
    ops approve AlphaWbaiFoo -y       # 跳过确认
    ops approve -u wbai               # 批量:wbai 所有 correlation-rejected 因子
    ops approve -u wbai -y            # 批量,跳过确认

用途:自动流水线只认业绩 + 低相关,不看数据使用覆盖。approve 是人工闸,
放行"业绩/相关性不占优、但扩了库内稀缺数据覆盖"的因子(配合
`ops list --filter-by field=X / tables=X` 反查覆盖缺口)。

仅适用于 last_fail_stage == correlation 的 REJECTED 因子;
其他失败阶段是质量/正确性问题,不允许 approve。

不重跑任何 check 阶段(dump/pnl/feature 在 correlation 失败时已保留),
仅 state 翻 ACTIVE。
""",
    )

    parser.add_argument("factor_name", nargs="?", default=None, type=str,
                        help="单因子名;省略时配合 -u 批量")
    parser.add_argument("--user", "-u", dest="user", default=None,
                        type=str, action=LowerAction,
                        help="按 author 过滤(批量)")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="跳过交互确认")
    parser.add_argument("--config-path", "-c", type=Path,
                        default=get_default_config_path())

    parser.set_defaults(func=run_approve)
