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
    ops sync push                  # 推送本地新文件 + state merge
    ops sync push --dry-run        # 干跑
    ops sync push --deep           # 等大小文件也走 etag 校验,捕捉内容漂移
    ops sync pull                  # 拉远端 state + 远端新增/更新的文件
    ops sync pull --deep           # 同上,等大小也比 etag
    ops sync status                # 本地 vs 远端 state 快速对比
    ops sync verify                # 三个数据目录文件级两端校验
    ops sync verify --deep         # 同上,等大小再比 etag(慢:读全部本地文件)
""",
    )

    sub = parser.add_subparsers(dest="action", required=True)

    for act in ("push", "pull"):
        p = sub.add_parser(act, help=f"{act} between local and remote")
        p.add_argument("--dry-run", action="store_true", help="只展示,不传输")
        p.add_argument("--force-state", action="store_true",
                       help="跳过 merge,用本地 state 直接覆盖远端(factor_state/metrics/datasources)")
        p.add_argument("--deep", action="store_true",
                       help="等大小文件再走 etag 比对捕捉内容漂移(慢)")
        p.add_argument("--config-path", "-c", type=Path, default=get_default_config_path())
        p.set_defaults(func=run_sync)

    p = sub.add_parser("status", help="基于 state 的快速对比(不扫数据目录)")
    p.add_argument("--config-path", "-c", type=Path, default=get_default_config_path())
    p.set_defaults(func=run_sync)

    p = sub.add_parser("verify", help="三个数据目录文件级两端校验")
    p.add_argument("--deep", action="store_true",
                   help="等大小文件再走 etag 比对(慢:本地需读全部文件算 md5)")
    p.add_argument("--config-path", "-c", type=Path, default=get_default_config_path())
    p.set_defaults(func=run_sync)
