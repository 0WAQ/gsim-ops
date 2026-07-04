import argparse
from pathlib import Path

from ops.infra.config import get_default_config_path
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
    parser.add_argument("--status", "-s", type=str, default=None,
                        choices=["submitted", "checking", "active", "rejected", "decaying", "retired"],
                        help="按状态过滤")
    parser.add_argument("--force", action="store_true", help="强制重写已打包因子")
    parser.add_argument("--dry-run", action="store_true", help="仅列出待打包因子,不执行")
    parser.add_argument("--no-verify", action="store_true", help="跳过抽样校验")
    parser.add_argument("--workers", "-w", type=int, default=10, help="并行进程数 (默认 10)")
    parser.add_argument("--config-path", "-c", type=Path, default=get_default_config_path())

    parser.set_defaults(func=run_pack)
