"""ops rm — soft-delete a factor.

Default: flip state to DELETED (a tombstone). Files on disk untouched, so
the factor can still be recovered manually or via `ops backfill` /
`ops status` if its meta.json + src remain.

`--force`: additionally drop local dump dir + feature .npy. src and pnl are
always preserved (mirrors the rejected-factor retention policy).

Tombstone propagates to other machines via `ops sync push` state merge —
their `ops list` will hide the factor too. No remote files are touched
here; bulk remote cleanup is left to a future `ops sync gc`.
"""
import shutil
from datetime import datetime
from pathlib import Path

from ops.infra.config import Config
from ops.infra.lock import factor_lock, FactorLocked
from ops.infra.store import default_store
from ops.core.state import FactorStatus
from ops.utils.logger.log import banner, bottom, info, warn, error, highlight


def _purge_artifacts(name: str, config: Config) -> list[str]:
    removed: list[str] = []
    dump_dir = config.alpha_dump / name
    if dump_dir.exists():
        shutil.rmtree(dump_dir)
        removed.append(f"alpha_dump/{name}")
    for v in ("v1", "v2"):
        f = config.alpha_feature / f"{name}.{v}.npy"
        if f.exists():
            f.unlink()
            removed.append(f"alpha_feature/{f.name}")
    return removed


def run_rm(args) -> None:
    name: str = args.factor_name
    config: Config = Config.load(args.config_path)
    store = default_store(config)

    rec = store.get(name)
    if rec is None:
        error(f"  ✘ 因子 {name} 不在 state 中")
        return
    if rec.status == FactorStatus.DELETED:
        warn(f"  ⚠ {name} 已是 deleted 状态")
        return

    banner(f"删除因子 {name}")
    highlight(f"  状态: {rec.status.value} → deleted")
    if args.force:
        highlight("  --force: 同步删除 dump + feature(保留 src/pnl)")
    else:
        info("  (软删除:磁盘文件保留,仅标记状态)")

    if not args.yes:
        ans = input("  确认? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            info("  已取消")
            return

    try:
        with factor_lock(name):
            now = datetime.now().isoformat(timespec="seconds")
            store.transition(name, FactorStatus.DELETED, deleted_at=now)
            info(f"  ✔ state 已标记 deleted ({now})")

            if args.force:
                removed = _purge_artifacts(name, config)
                if removed:
                    for r in removed:
                        info(f"  ✔ 已删除 {r}")
                else:
                    info("  · 无 dump / feature 残留可清")
    except FactorLocked:
        error(f"  ✘ {name} 被另一个进程占用,稍后再试")
        return
    bottom()
