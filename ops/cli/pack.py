import argparse

from ops.cli.common import STATUS_CHOICES, add_config_arg, mark_write
from ops.services.pack import run_pack


def add_pack_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "pack",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="聚合 alpha_dump 为 alpha_feature 矩阵",
        epilog="""\
Example:
    ops pack                         # 增量打包未处理的因子
    ops pack --factor AlphaXxx       # 只打包指定因子
    ops pack --force                 # 强制重写全部
""",
    )

    parser.add_argument("--factor", "-f", type=str, default=None, help="只处理指定因子")
    parser.add_argument("--user", "-u", type=str, default=None, help="按作者过滤")
    # choices 从 FactorStatus 派生:别手抄字符串(会与 enum/DB 约束漂移)
    parser.add_argument("--status", "-s", type=str, default=None,
                        choices=list(STATUS_CHOICES),
                        help="按状态过滤")
    parser.add_argument("--force", action="store_true", help="强制重写已打包因子")
    parser.add_argument("--dry-run", action="store_true", help="仅列出待打包因子,不执行")
    parser.add_argument("--no-verify", action="store_true", help="跳过抽样校验")
    parser.add_argument("--workers", "-w", type=int, default=10, help="并行进程数 (默认 10)")
    add_config_arg(parser)

    mark_write(parser)

    parser.set_defaults(func=run_pack)
