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
    ops sync push                  # 推送 data + state 到 remote
    ops sync push --dry-run        # 干跑,展示差异
    ops sync push --state-only     # 只推 state
    ops sync push --data-only      # 只推 data
    ops sync pull                  # 从 remote 拉回 data + state
    ops sync status                # rclone check 对比本地与 remote
""",
    )

    sub = parser.add_subparsers(dest="action", required=True)

    for act in ("push", "pull"):
        p = sub.add_parser(act, help=f"{act} between local and remote")
        p.add_argument("--dry-run", action="store_true", help="只展示,不传输")
        scope = p.add_mutually_exclusive_group()
        scope.add_argument("--data-only", action="store_true", help="只同步 data")
        scope.add_argument("--state-only", action="store_true", help="只同步 state")
        p.add_argument("--config-path", "-c", type=Path, default=get_default_config_path())
        p.set_defaults(func=run_sync)

    p = sub.add_parser("status", help="对比本地与 remote 的差异")
    p.add_argument("--config-path", "-c", type=Path, default=get_default_config_path())
    p.set_defaults(func=run_sync)
