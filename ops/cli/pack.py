import argparse

from ops.cli.common import STATUS_CHOICES, add_config_arg
from ops.services.pack import run_pack


def add_pack_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "pack",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Example:
    ops pack                         # 增量打包未处理的因子
    ops pack --dry-run               # 预览待打包因子
    ops pack --force                 # 强制重写全部
    ops pack --factor AlphaXxx       # 只打包指定因子
    ops pack -u wbai                 # 只打包 wbai 的因子
    ops pack -u wbai -s active       # 只打包 wbai 的 active 因子
    ops pack --workers 16            # 并行度
    ops pack --no-verify             # 跳过抽样校验
""",
    )

    parser.add_argument("--factor", "-f", type=str, default=None, help="只处理指定因子")
    parser.add_argument("--user", "-u", type=str, default=None, help="按作者过滤")
    # choices 从 FactorStatus 派生:手抄字符串曾与 enum/DB 约束漂移
    # (含 DB 拒收的 decaying/retired,full-review 第三部分 S10)
    parser.add_argument("--status", "-s", type=str, default=None,
                        choices=list(STATUS_CHOICES),
                        help="按状态过滤")
    parser.add_argument("--force", action="store_true", help="强制重写已打包因子")
    parser.add_argument("--dry-run", action="store_true", help="仅列出待打包因子,不执行")
    parser.add_argument("--no-verify", action="store_true", help="跳过抽样校验")
    parser.add_argument("--workers", "-w", type=int, default=10, help="并行进程数 (默认 10)")
    add_config_arg(parser)

    parser.set_defaults(func=run_pack)
