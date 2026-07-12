from pathlib import Path

from ops.core.datasource import build_npy_index
from ops.core.factor import FactorIdentity
from ops.core.factormeta import FactorMeta, parse_factor
from ops.core.paths import META_FILENAME
from ops.core.state import FactorStatus
from ops.infra.config import Config
from ops.infra.lock import FactorLocked, factor_lock
from ops.infra.repository import FactorRepository
from ops.utils.clock import now_iso
from ops.utils.printer import banner, bottom, error, info, warn


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
    repo = FactorRepository(config)

    banner("存量回填" + (" (dry-run)" if dry_run else ""))

    dirs = _iter_alpha_src(config)
    info(f"扫描到 {len(dirs)} 个因子目录")

    npy_index = None
    if not dry_run and dirs:
        info("构建 npy_index ...")
        npy_index = build_npy_index(config.nio_data_path)

    created = skipped = failed = state_added = locked = 0
    failures: list[tuple[str, str]] = []
    now = now_iso()

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

        # info + state 记录:repo.register 一个事务原子写(legacy 因子已在库,
        # status=ACTIVE + entered_at=now;submitted_at 留空 —— 真实提交时间不可知)。
        # 全程持因子锁(2026-07-12 对抗评审):backfill 原是全库唯一不持锁的
        # 状态写入方,会击穿 doctor 五道闸的 TOCTOU 防线(锁内重验挡不住
        # 无锁写入者在"重验通过 → 删除"窗口里把因子登记成 ACTIVE)。
        # repository.register 的契约本就要求调用方持锁,此处补齐。
        name = msg or factor_dir.name
        if not dry_run:
            try:
                with factor_lock(name, config):
                    if repo.record(name) is None:
                        meta_path = factor_dir / META_FILENAME
                        try:
                            meta = FactorMeta.load(meta_path)
                            author = meta.author or "unknown"
                            discovery_method = meta.discovery_method or "backfill"
                        except Exception:
                            author = "unknown"
                            discovery_method = "backfill"

                        repo.register(
                            FactorIdentity(
                                name=name,
                                author=author,
                                discovery_method=discovery_method,
                                created_at=now,
                            ),
                            status=FactorStatus.ACTIVE,
                            entered_at=now,
                            op="backfill",  # + 自动 'entered'(status=ACTIVE)
                        )
                        state_added += 1
            except FactorLocked:
                locked += 1
                warn(f"  {name}: 因子锁被持有,跳过(重跑 backfill 再补)")

    banner("回填汇总")
    info(f"✔ meta.json 新建 : {created:>4}")
    info(f"  meta.json 已存在 : {skipped:>4}")
    info(f"  state record 新增 : {state_added:>4}")
    if locked:
        info(f"  锁跳过 : {locked:>4}")
    if failed:
        error(f"✘ 失败 : {failed:>4}")
        for name, reason in failures[:20]:
            error(f"  - {name}: {reason}")
        if len(failures) > 20:
            error(f"  ... +{len(failures) - 20} more")
    bottom()
