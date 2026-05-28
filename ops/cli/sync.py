import argparse
from pathlib import Path

from ops.infra.config import get_default_config_path
from ops.services.sync import run_sync


def add_sync_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "sync",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Example:
    ops sync push                  # 推送本地新变更 + state merge
    ops sync push --dry-run        # 干跑
    ops sync pull                  # 拉远端 state + 本地缺失的因子
    ops sync status                # 本地 vs 远端 state 快速对比
    ops sync verify                # rclone check 全量校验(慢)

首次在新机器上跑 push/pull 都会自动处理(扫盘建 manifest / 空盘全量拉)。
""",
    )

    sub = parser.add_subparsers(dest="action", required=True)

    for act in ("push", "pull"):
        p = sub.add_parser(act, help=f"{act} between local and remote")
        p.add_argument("--dry-run", action="store_true", help="只展示,不传输")
        p.add_argument("--force-state", action="store_true",
                       help="跳过 merge,用本地 state 直接覆盖远端(factor_state/metrics/datasources)")
        p.add_argument("--config-path", "-c", type=Path, default=get_default_config_path())
        p.set_defaults(func=run_sync)

    p = sub.add_parser("status", help="基于 manifest + 远端 state 的快速对比")
    p.add_argument("--config-path", "-c", type=Path, default=get_default_config_path())
    p.set_defaults(func=run_sync)

    p = sub.add_parser("verify", help="rclone check 全量校验(慢)")
    p.add_argument("--config-path", "-c", type=Path, default=get_default_config_path())
    p.set_defaults(func=run_sync)
