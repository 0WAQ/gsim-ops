import argparse
from pathlib import Path
from datetime import datetime

from ops.utils.utils import LowerAction
from ops.infra.config import get_default_config_path
from ops.services.resubmit import run_resubmit


def add_resubmit_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "resubmit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="已有因子提交新代码(从 dropbox,version += 1)",
        epilog="""\
Example:
    ops resubmit -u wbai -s 20260401 -f AlphaWbaiFoo   # 单因子
    ops resubmit -u wbai -s 20260401                    # 该日期下所有已存在因子
    ops resubmit -u wbai -s 20260401 -e 20260405        # 日期范围

因子名必须已存在于 state 中,否则拒绝(提示用 ops submit)。
新代码覆盖到 staging,version += 1,旧 alpha_src 代码保留作为对比。
dump / feature / pnl 保留。
""",
    )

    today = datetime.today()
    parser.add_argument("--user", "-u", required=True, type=str, action=LowerAction)
    parser.add_argument("--start-date", "-s", default=today.strftime("%Y%m%d"))
    parser.add_argument("--end-date", "-e", default=None)
    parser.add_argument("--factor-name", "-f", type=str, default=None)
    parser.add_argument("-y", "--yes", action="store_true",
                        help="跳过交互确认")
    parser.add_argument("--config-path", "-c", type=Path,
                        default=get_default_config_path())

    parser.set_defaults(func=run_resubmit)
