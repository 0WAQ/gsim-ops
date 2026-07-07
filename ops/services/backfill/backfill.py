from datetime import datetime
from pathlib import Path

from ops.infra.config import Config
from ops.infra.store import default_store
from ops.infra.info import default_info_store, FactorInfo
from ops.core.state import FactorRecord, FactorStatus
from ops.core.factormeta import FactorMeta
from ops.services.submit.parser import parse_factor
from ops.services.list.datasource import _build_npy_index
from ops.utils.printer import banner, bottom, info, warn, error, highlight


META_FILENAME = "meta.json"


def _iter_alpha_src(config: Config) -> list[Path]:
    if not config.alpha_src.exists():
        return []
    return [d for d in config.alpha_src.iterdir()
            if d.is_dir() and d.name.startswith("Alpha")]


def backfill_one(factor_dir: Path, config: Config, dry_run: bool,
                 npy_index: dict | None = None) -> tuple[str, str | None]:
    """Returns (status, error_msg). status in {created, skipped, failed}."""
    meta_path = factor_dir / META_FILENAME
    if meta_path.exists():
        try:
            meta = FactorMeta.load(meta_path)
            return ("skipped", meta.name)
        except Exception as e:
            return ("failed", f"existing meta.json broken: {e}")

    if dry_run:
        return ("created", factor_dir.name)

    try:
        meta = parse_factor(factor_dir, config,
                            submitted_by=None, submitted_at=None,
                            npy_index=npy_index)
    except Exception as e:
        return ("failed", str(e))

    meta.save(meta_path)
    return ("created", meta.name)


def run_backfill(args):
    config_path: Path = args.config_path
    dry_run: bool = args.dry_run

    config = Config.load(config_path)
    store = default_store(config)
    info_store = default_info_store(config)

    banner("存量回填" + (" (dry-run)" if dry_run else ""))

    dirs = _iter_alpha_src(config)
    info(f"扫描到 {len(dirs)} 个因子目录")

    npy_index = None
    if not dry_run and dirs:
        info("构建 npy_index ...")
        npy_index = _build_npy_index(config.nio_data_path)

    created = skipped = failed = state_added = 0
    failures: list[tuple[str, str]] = []
    now = datetime.now().isoformat(timespec="seconds")

    for factor_dir in dirs:
        status, msg = backfill_one(factor_dir, config, dry_run, npy_index=npy_index)
        if status == "created":
            created += 1
        elif status == "skipped":
            skipped += 1
        else:
            failed += 1
            failures.append((factor_dir.name, msg or ""))
            continue

        # state record + info record
        name = msg or factor_dir.name
        if not dry_run and store.get(name) is None:
            meta_path = factor_dir / META_FILENAME
            try:
                meta = FactorMeta.load(meta_path)
                author = meta.author or "unknown"
                discovery_method = meta.discovery_method or "backfill"
            except Exception:
                author = "unknown"
                discovery_method = "backfill"

            # 先写 factor_info
            info_store.upsert(FactorInfo(
                name=name,
                author=author,
                discovery_method=discovery_method,
                created_at=now,
            ))

            # 再写 factor_state
            store.put(FactorRecord(
                name=name,
                status=FactorStatus.ACTIVE,
                version=1,
                updated_at=now,
                entered_at=now,
            ))
            state_added += 1

    banner("回填汇总")
    info(f"✔ meta.json 新建 : {created:>4}")
    info(f"  meta.json 已存在 : {skipped:>4}")
    info(f"  state record 新增 : {state_added:>4}")
    if failed:
        error(f"✘ 失败 : {failed:>4}")
        for name, reason in failures[:20]:
            error(f"  - {name}: {reason}")
        if len(failures) > 20:
            error(f"  ... +{len(failures) - 20} more")
    bottom()
