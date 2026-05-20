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
    ops pack --force                 # 强制重写全部
    ops pack --factor AlphaXxx       # 只打包指定因子
    ops pack --workers 16            # 并行度
    ops pack --no-verify             # 跳过抽样校验
""",
    )

    parser.add_argument("--factor", "-f", type=str, default=None, help="只处理指定因子")
    parser.add_argument("--force", action="store_true", help="强制重写已打包因子")
    parser.add_argument("--no-verify", action="store_true", help="跳过抽样校验")
    parser.add_argument("--workers", "-w", type=int, default=10, help="并行进程数 (默认 10)")
    parser.add_argument("--config-path", "-c", type=Path, default=get_default_config_path())

    parser.set_defaults(func=run_pack)
