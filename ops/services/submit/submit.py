import shutil
from pathlib import Path
from datetime import datetime

from ops.infra.config import Config
from ops.utils.func import date_range
from ops.utils.logger.log import info, warn, error, highlight, banner, bottom
from ops.infra.store import default_store, StateStore
from ops.core.state import FactorRecord, FactorStatus
from .parser import parse_factor
from .normalize import normalize_factor_xml


META_FILENAME = "meta.json"


def _iter_dropbox_dirs(config: Config, users: list[str], start: str, end: str,
                       factor_name: str | None) -> list[tuple[str, Path]]:
    """Scan dropbox_path (source, read-only) and return (user, factor_dir) pairs."""
    dropbox = config.dropbox_path
    out: list[tuple[str, Path]] = []

    if factor_name is not None:
        assert start == end, "start must equal to end when --factor-name given"
        for user in users:
            d = dropbox / user / start / factor_name
            if d.exists() and d.is_dir():
                out.append((user, d))
        return out

    for user in users:
        root = dropbox / user
        if not root.exists():
            continue
        for date in date_range(start, end):
            date_path = root / date
            if not date_path.is_dir():
                continue
            for factor_dir in date_path.iterdir():
                if factor_dir.is_dir() and factor_dir.name.startswith("Alpha"):
                    out.append((user, factor_dir))
    return out


def copy_to_staging(config: Config, factor_dirs: list[Path]) -> list[Path]:
    """Copy factor dirs into staging/AlphaXxx/ (flat).

    Existing staging dir for the same factor is overwritten.
    Returns the new paths under staging.
    """
    config.staging.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for src in factor_dirs:
        dst = config.staging / src.name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        out.append(dst)
    return out


def submit_one(staging_dir: Path, submitted_by: str, config: Config,
               store: StateStore) -> bool:
    submitted_at = datetime.now().isoformat(timespec="seconds")
    try:
        normalize_factor_xml(staging_dir)
        meta = parse_factor(staging_dir, config,
                            submitted_by=submitted_by, submitted_at=submitted_at)
    except SyntaxError as e:
        error(f"  ✘  {staging_dir.name} syntax error: {e}")
        return False
    except Exception as e:
        error(f"  ✘  {staging_dir.name} parse failed: {e}")
        return False

    meta_path = staging_dir / META_FILENAME
    meta.save(meta_path)

    record = FactorRecord(
        name=meta.name,
        author=meta.author or submitted_by,
        status=FactorStatus.SUBMITTED,
        updated_at=submitted_at,
        submitted_at=submitted_at,
        submitted_by=submitted_by,
    )
    store.put(record)

    info(f"  ✔  {meta.name} → {meta_path}")
    return True


def run_submit(args):
    users: list[str] = [args.user]
    start: str = args.start_date
    end: str = args.end_date or start
    factor_name: str | None = args.factor_name
    config_path: Path = args.config_path

    config = Config.load(config_path)
    store = default_store()

    banner("因子提交")
    found = _iter_dropbox_dirs(config, users, start, end, factor_name)
    if not found:
        warn("没找到任何因子目录")
        bottom()
        return

    user_of = {d: user for user, d in found}
    src_dirs = [d for _, d in found]
    staging_dirs = copy_to_staging(config, src_dirs)

    passed = failed = 0
    for src, staged in zip(src_dirs, staging_dirs):
        submitted_by = user_of[src]
        print("submitting ", end=""); highlight(f"{staged.name}")
        ok = submit_one(staged, submitted_by, config, store)
        if ok:
            passed += 1
        else:
            failed += 1

    banner("提交汇总")
    info(f"✔ 成功 : {passed:>4}")
    if failed > 0:
        error(f"✘ 失败 : {failed:>4}")
    bottom()
