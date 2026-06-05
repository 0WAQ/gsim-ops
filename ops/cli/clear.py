import argparse
from pathlib import Path

from ops.utils.utils import LowerAction
from ops.infra.config import get_default_config_path
from ops.services.clear import run_clear


def add_clear_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "clear",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="清理 staging 里的孤儿目录(state 无 record)",
        epilog="""\
Example:
    ops clear AlphaLhwFoo         # 单孤儿,询问确认
    ops clear                     # 扫全部孤儿,询问确认
    ops clear -u lhw              # 仅 lhw 推断作者的孤儿
    ops clear -u lhw -y           # 跳过确认

孤儿 = staging/<name>/ 存在 但 state 无 record。
来源: ops submit 中 parse_factor 失败留下的残骸。

state 中有 record 的(SUBMITTED / CHECKING)请用 ops cancel。
""",
    )

    parser.add_argument("factor_name", nargs="?", default=None, type=str,
                        help="单个孤儿目录名;省略时扫全部")
    parser.add_argument("--user", "-u", dest="user", default=None,
                        type=str, action=LowerAction,
                        help="按推断的 author 过滤(批量)")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="跳过交互确认")
    parser.add_argument("--config-path", "-c", type=Path,
                        default=get_default_config_path())

    parser.set_defaults(func=run_clear)
