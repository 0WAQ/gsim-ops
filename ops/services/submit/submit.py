import shutil
from pathlib import Path
from datetime import datetime

from ops.infra.config import Config
from ops.utils.func import date_range
from ops.utils.printer import info, warn, error, highlight, banner, bottom, progress
from ops.infra.store import default_store, StateStore
from ops.infra.info import default_info_store, FactorInfo
from ops.infra.lock import factor_lock, FactorLocked
from ops.core.state import FactorRecord, FactorStatus
from ops.services.list.datasource import _build_npy_index
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


def _copy_one_to_staging(config: Config, src: Path) -> Path:
    """Copy a single factor dir into staging/AlphaXxx/ (flat).

    If staging dir already exists, it's an orphan (state has no record — see
    `ops clear`). We warn and overwrite rather than fail.
    """
    config.staging.mkdir(parents=True, exist_ok=True)
    dst = config.staging / src.name
    if dst.exists():
        warn(f"  ⚠  {dst.name} staging 已存在(疑似 parse 失败遗留的 orphan),覆盖重写")
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return dst


def copy_to_staging(config: Config, factor_dirs: list[Path]) -> list[Path]:
    """Batch wrapper over _copy_one_to_staging. Kept as a public helper; submit
    itself interleaves copy + lock + parse per factor instead.
    """
    return [_copy_one_to_staging(config, src) for src in factor_dirs]


def submit_one(staging_dir: Path, submitted_by: str, config: Config,
               store: StateStore, overwrite: bool = False,
               npy_index: dict | None = None) -> str:
    """Submit one factor from staging into state. Returns "pass" | "skip" | "fail".

    2026-07-06 重构: 拆分写入 factor_info (author/discovery_method) + factor_state (状态)。

    New factor (no state record)     -> insert info + put state, version=1.
    Existing factor + overwrite=True -> transition state to SUBMITTED, version += 1
                                        (info 不变，只更新 state).
    Existing factor + overwrite=False -> "skip" (defensive; run_submit normally
                                        filters these out before copy).
    """
    submitted_at = datetime.now().isoformat(timespec="seconds")

    py_files = sorted(staging_dir.glob("*.py"))
    xml_files = sorted(staging_dir.glob("*.xml"))
    if len(py_files) != 1 or len(xml_files) != 1:
        error(f"  ✘  {staging_dir.name} 文件数不合规: "
              f".py={[p.name for p in py_files]}, .xml={[x.name for x in xml_files]} "
              f"(各需恰好 1 个,请清理多余文件后重提)")
        return "fail"

    try:
        normalize_factor_xml(staging_dir)
        meta = parse_factor(staging_dir, config,
                            submitted_by=submitted_by, submitted_at=submitted_at,
                            npy_index=npy_index)
    except SyntaxError as e:
        error(f"  ✘  {staging_dir.name} syntax error: {e}")
        return "fail"
    except Exception as e:
        error(f"  ✘  {staging_dir.name} parse failed: {e}")
        return "fail"

    if meta.discovery_method not in ("automated", "manual"):
        error(f"  ✘  {staging_dir.name} discovery_method 缺失或非法: "
              f"{meta.discovery_method!r}(须为 automated / manual,请在 "
              f"<Description discovery_method=...> 补全)")
        return "fail"

    # Authoritative existence check under the caller's factor_lock.
    rec = store.get(meta.name)
    if rec is not None and not overwrite:
        return "skip"

    meta_path = staging_dir / META_FILENAME
    meta.save(meta_path)

    info_store = default_info_store(config)

    if rec is None:
        # 新因子: 写 factor_info + factor_state
        info_store.upsert(FactorInfo(
            name=meta.name,
            author=meta.author or submitted_by,
            discovery_method=meta.discovery_method,
            created_at=submitted_at,
        ))
        store.put(FactorRecord(
            name=meta.name,
            status=FactorStatus.SUBMITTED,
            updated_at=submitted_at,
            submitted_at=submitted_at,
        ))
        info(f"  ✔  {meta.name} → submitted (version=1)")
    else:
        # 已存在: 只更新 state (version += 1)，info 不变
        new_version = rec.version + 1
        store.transition(meta.name, FactorStatus.SUBMITTED,
                         submitted_at=submitted_at,
                         version=new_version)
        info(f"  ✔  {meta.name} → submitted (version={new_version},覆盖新代码)")

    if meta.author and meta.author != submitted_by:
        warn(f"  ⚠  {meta.name}: 推断 author={meta.author!r} 与 submitter={submitted_by!r} 不一致,"
             f"后续 -u 过滤按 author,可能漏掉本因子")

    return "pass"



def run_submit(args):
    users: list[str] = [args.user]
    start: str = args.start_date
    end: str = args.end_date or start
    factor_name: str | None = args.factor_name
    overwrite: bool = args.overwrite
    config_path: Path = args.config_path

    config = Config.load(config_path)
    store = default_store(config)

    banner("因子提交")
    found = _iter_dropbox_dirs(config, users, start, end, factor_name)

    if not found:
        warn("没找到任何因子目录")
        bottom()
        return

    # 默认只提交新因子: 已入库的静默跳过 (--overwrite 才覆盖成新代码 version+1)。
    # 这里先粗过滤省掉 copy + 锁开销; submit_one 在锁内还会按 overwrite 权威判定。
    to_process: list[tuple[str, Path]] = []
    skipped = 0
    for user, src in found:
        existing = store.get(src.name)
        if existing is not None and not overwrite:
            info(f"  ⤼  {src.name} 已入库 (status={existing.status.value}),跳过")
            skipped += 1
            continue
        to_process.append((user, src))

    if not to_process:
        warn("没有可提交的因子")
        if skipped > 0:
            warn(f"⤼ 跳过 : {skipped:>4}  (已入库,--overwrite 覆盖成新代码)")
        bottom()
        return

    # npy_index 全量 scan 一次, 整个 batch 共享, 避免 N 个因子 N 次扫盘
    npy_index = _build_npy_index(config.nio_data_path)

    passed = failed = 0
    for submitted_by, src in to_process:
        name = src.name
        progress("submitting ", name)
        staged: Path | None = None
        result = "fail"
        try:
            with factor_lock(name, config):
                staged = _copy_one_to_staging(config, src)
                result = submit_one(staged, submitted_by, config, store,
                                    overwrite=overwrite, npy_index=npy_index)
                if result != "pass":
                    # skip (并发下已入库且非 overwrite) / fail (parse / 文件数不合规):
                    # 回滚 staging,避免留下 orphan 等下次被静默覆盖
                    shutil.rmtree(staged, ignore_errors=True)
        except FactorLocked:
            warn(f"  ⚠  {name} 已被另一个进程占用,跳过")
            result = "fail"
        except Exception as e:
            # meta.save / store.put / copytree 等不可控异常: 同样回滚
            error(f"  ✘  {name} 提交异常: {e}")
            if staged is not None:
                shutil.rmtree(staged, ignore_errors=True)
            result = "fail"

        if result == "pass":
            passed += 1
        elif result == "skip":
            skipped += 1
        else:
            failed += 1

    banner("提交汇总")
    info(f"✔ 成功 : {passed:>4}")
    if failed > 0:
        error(f"✘ 失败 : {failed:>4}")
    if skipped > 0:
        warn(f"⤼ 跳过 : {skipped:>4}  (已入库,--overwrite 覆盖成新代码)")
    bottom()
