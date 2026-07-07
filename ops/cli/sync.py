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
    ops sync push                  # 文件级 etag 增量推送 + state merge
    ops sync push --dry-run        # 干跑
    ops sync push --deep           # 忽略本地 etag 缓存,重算所有本地 etag(慢)
    ops sync pull                  # 拉远端 state + 远端新增/变更的文件
    ops sync pull --deep           # 同上,忽略本地 etag 缓存重算
    ops sync status                # 本地 vs 远端 state 快速对比
    ops sync verify                # 三个数据目录 etag 级两端校验
    ops sync verify --deep         # 同上,忽略缓存重算(慢:全本地文件读盘)
""",
    )

    sub = parser.add_subparsers(dest="action", required=True)

    for act in ("push", "pull"):
        p = sub.add_parser(act, help=f"{act} between local and remote")
        p.add_argument("--dry-run", action="store_true", help="只展示,不传输")
        if act == "push":
            # 仅 push 支持:原先 for 循环把这两个旗标盲目复制给了 pull,而
            # pull() 的签名根本没有这两个参数 —— 解析通过、静默无效
            # (full-review 第三部分 V 表)。
            p.add_argument("--force-state", action="store_true",
                           help="跳过 merge,用本地 state 直接覆盖远端(factor_state/metrics/datasources)")
            p.add_argument("--force-overwrite", action="store_true",
                           help="强制覆盖远端 differ 文件(etag 不同也传)")
        p.add_argument("--deep", action="store_true",
                       help="忽略本地 etag 缓存,强制重算所有本地 etag(慢)")
        p.add_argument("--config-path", "-c", type=Path, default=get_default_config_path())
        p.set_defaults(func=run_sync)

    p = sub.add_parser("status", help="基于 state 的快速对比(不扫数据目录)")
    p.add_argument("--config-path", "-c", type=Path, default=get_default_config_path())
    p.set_defaults(func=run_sync)

    p = sub.add_parser("verify", help="三个数据目录文件级两端校验")
    p.add_argument("--deep", action="store_true",
                   help="忽略本地 etag 缓存,强制重算所有本地 etag(慢)")
    p.add_argument("--config-path", "-c", type=Path, default=get_default_config_path())
    p.set_defaults(func=run_sync)
