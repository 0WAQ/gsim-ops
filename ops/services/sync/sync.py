"""S3-based cross-server factor library sync.

Remote layout (S3 bucket):
    <library_id>/alpha_src/<name>/...
    <library_id>/alpha_pnl/<name>
    <library_id>/alpha_feature/<name>.v1.npy
    <library_id>/.state/{factor_state,metrics,datasources}.json

Local alpha_dump is a local-only intermediate product (not synced).
"""
import json
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

from ops.infra.config import Config
from ops.infra.s3 import S3Client
from ops.infra.cache import cache_path
from ops.services.sync import etag_cache
from ops.services.sync.merge import MERGERS
from ops.services.sync.diff import (
    DirDiff, walk_local, list_remote, diff, newer_side,
)
from ops.utils.logger.log import banner, bottom, info, warn, error, highlight

STATE_FILES = ("factor_state.json", "metrics.json", "datasources.json")
DATA_DIRS = ("alpha_src", "alpha_pnl", "alpha_feature")


def _make_s3(config: Config) -> S3Client:
    if not all([config.s3_endpoint_url, config.s3_access_key_id,
                config.s3_secret_access_key, config.s3_bucket]):
        raise RuntimeError("sync.s3 未配置完整")
    return S3Client(
        endpoint_url=config.s3_endpoint_url,  # type: ignore[arg-type]
        access_key_id=config.s3_access_key_id,  # type: ignore[arg-type]
        secret_access_key=config.s3_secret_access_key,  # type: ignore[arg-type]
        bucket=config.s3_bucket,  # type: ignore[arg-type]
    )


def _pfx(config: Config) -> str:
    return config.library_id

# PLACEHOLDER_CHUNK_2


def _merge_states(config: Config, s3: S3Client, pfx: str,
                  *, upload: bool, dry_run: bool) -> int:
    failed = 0
    for fname in STATE_FILES:
        local = cache_path(config.library_id, fname)
        with tempfile.TemporaryDirectory(prefix="ops-state-") as td:
            tmp = Path(td) / fname
            ok = s3.download(f"{pfx}/.state/{fname}", tmp)
            if not ok:
                info(f"  · {fname}: 远端不存在,跳过 merge")
            else:
                try:
                    added, updated = MERGERS[fname](local, tmp)
                    info(f"  ✔ {fname}: +{added}, ~{updated}")
                except Exception as e:
                    error(f"  ✘ {fname} merge: {e}")
                    failed += 1
                    continue
            if upload and local.exists() and not dry_run:
                try:
                    s3.upload(local, f"{pfx}/.state/{fname}")
                except Exception as e:
                    error(f"  ✘ {fname} upload: {e}")
                    failed += 1
    return failed

# PLACEHOLDER_CHUNK_3


def _split_for_push(result: DirDiff) -> tuple[list[str], list[str]]:
    """From a DirDiff, decide what to upload vs. what to flag as conflict.

    Upload: files only on local + differ-where-local-is-newer.
    Conflict: differ where remote is newer or tie — we never overwrite
    remote work that we don't have a known-newer version of.
    `only_remote` is silently ignored on push (deletion is gc's job).
    """
    to_upload = list(result.only_local)
    conflicts: list[str] = []
    for rel in result.differ:
        side = newer_side(rel, result)
        if side == "local":
            to_upload.append(rel)
        else:
            conflicts.append(rel)
    to_upload.sort()
    return to_upload, conflicts


def _push_dir(name: str, local_root: Path, remote_prefix: str,
              s3: S3Client, *, library_id: str, dry_run: bool,
              recompute: bool = False) -> int:
    """Diff one data dir between local and S3, upload missing/local-newer
    files, warn on conflicts. Returns number of failed uploads.

    `recompute=True` ignores the local etag cache for this walk (—deep).
    """
    cache = etag_cache.load(library_id)
    local = walk_local(local_root, subdir=name, cache=cache,
                       recompute=recompute)
    remote = list_remote(s3, remote_prefix)
    result = diff(local, remote)
    to_upload, conflicts = _split_for_push(result)
    info(f"  本地: {len(local)}  远端: {len(remote)}  "
         f"一致: {len(result.identical)}  待传: {len(to_upload)}  "
         f"冲突: {len(conflicts)}" + ("  [recompute]" if recompute else ""))
    if conflicts:
        warn(f"  ⚠ {len(conflicts)} 个文件远端更新或 mtime 持平,跳过避免覆盖")
        for rel in conflicts[:5]:
            highlight(f"    ≠ {name}/{rel}")
        if len(conflicts) > 5:
            highlight(f"    … 另 {len(conflicts) - 5} 个")
    etag_cache.prune(cache, name, set(local.keys()))
    if not to_upload or dry_run:
        etag_cache.save(library_id, cache)
        return 0

    updates: list[tuple[str, float, int, str]] = []
    updates_lock = threading.Lock()

    def _upload_one(rel: str) -> str | None:
        try:
            remote_mtime = s3.upload(local_root / rel, f"{remote_prefix}/{rel}")
            if remote_mtime is not None:
                try:
                    os.utime(local_root / rel, (remote_mtime, remote_mtime))
                except OSError:
                    pass
                lo = local.get(rel)
                if lo is not None and lo.etag:
                    with updates_lock:
                        updates.append((rel, remote_mtime, lo.size, lo.etag))
            return None
        except Exception as e:
            return f"{rel}: {e}"

    failed_count = 0
    progress = tqdm(total=len(to_upload), desc=f"  {name}", unit="file")
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_upload_one, rel): rel for rel in to_upload}
        for fut in as_completed(futures):
            progress.update(1)
            err = fut.result()
            if err:
                failed_count += 1
                warn(f"    ✘ {err}")
    progress.close()

    for rel, mtime, size, etag in updates:
        etag_cache.put(cache, name, rel, mtime, size, etag)
    etag_cache.save(library_id, cache)
    return failed_count


def _load_state(path: Path) -> dict:
    """Load a state JSON file. Returns {} on missing/error."""
    if not path.exists():
        return {}
    try:
        with path.open("r") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _state_behind(local: dict, remote: dict) -> list[str]:
    """Names where remote.updated_at is strictly newer than local's.

    Treats missing-local-key as behind. Missing-remote-key never behind.
    Missing updated_at on either side is treated as epoch — so legacy
    entries don't trigger spurious behind reports.
    """
    out: list[str] = []
    for name, ro in remote.items():
        if not isinstance(ro, dict):
            continue
        lo = local.get(name)
        if not isinstance(lo, dict):
            out.append(name)
            continue
        if ro.get("updated_at", "") > lo.get("updated_at", ""):
            out.append(name)
    return out


def push(config: Config, *, dry_run: bool = False, force_state: bool = False,
         deep: bool = False) -> int:
    s3 = _make_s3(config)
    pfx = _pfx(config)
    library_id = config.library_id
    failed = 0

    if not force_state:
        banner("pre-push check")
        with tempfile.TemporaryDirectory(prefix="ops-state-") as td:
            tmp = Path(td) / "factor_state.json"
            ok = s3.download(f"{pfx}/.state/factor_state.json", tmp)
            if ok:
                local_state = _load_state(cache_path(library_id, "factor_state.json"))
                remote_state = _load_state(tmp)
                behind = _state_behind(local_state, remote_state)
                if behind:
                    error(f"  ✘ 远端有 {len(behind)} 个因子的 updated_at 比本地新,请先 pull")
                    for n in behind[:5]:
                        highlight(f"    < {n}")
                    if len(behind) > 5:
                        highlight(f"    … 另 {len(behind) - 5} 个")
                    return 1
                info(f"  ✔ 本地 state 不落后于远端 ({len(remote_state)} 个因子)")
            else:
                info("  · 远端 state 不存在,跳过检查")

    for d in DATA_DIRS:
        banner(f"push {d}" + (" (recompute)" if deep else ""))
        failed += _push_dir(d, getattr(config, d), f"{pfx}/{d}",
                            s3, library_id=library_id, dry_run=dry_run,
                            recompute=deep)

    banner("push state" + (" (force)" if force_state else " (merge)"))
    if force_state:
        failed += _force_push_states(config, s3, pfx, dry_run=dry_run)
    else:
        failed += _merge_states(config, s3, pfx, upload=True, dry_run=dry_run)
    return failed

# PLACEHOLDER_CHUNK_6


def _force_push_states(config: Config, s3: S3Client, pfx: str,
                       *, dry_run: bool) -> int:
    failed = 0
    for fname in STATE_FILES:
        local = cache_path(config.library_id, fname)
        if not local.exists():
            continue
        if dry_run:
            continue
        try:
            s3.upload(local, f"{pfx}/.state/{fname}")
            info(f"  ✔ {fname} 已上传(force)")
        except Exception as e:
            error(f"  ✘ {fname}: {e}")
            failed += 1
    return failed


def _extract_factor_name(rel: str, subdir: str) -> str:
    """Map a relpath under one data dir back to its factor name.

    alpha_src/<name>/...           → name = first segment
    alpha_pnl/<name>               → name = the whole basename
    alpha_feature/<name>.v{1,2}.npy → name = basename without .v?.npy suffix
    """
    if subdir == "alpha_src":
        return rel.split("/", 1)[0].split(os.sep, 1)[0]
    if subdir == "alpha_pnl":
        return rel
    if subdir == "alpha_feature":
        for suf in (".v1.npy", ".v2.npy"):
            if rel.endswith(suf):
                return rel[:-len(suf)]
        return rel
    return rel


def _factor_status(state: dict, name: str) -> str:
    """Return the status string for `name` in factor_state.json, or '' if absent."""
    entry = state.get(name)
    if isinstance(entry, dict):
        return str(entry.get("status", ""))
    return ""


def _split_for_pull(result: DirDiff, subdir: str,
                    state: dict) -> tuple[list[str], list[str], list[str]]:
    """From a DirDiff, decide what to download, what to skip due to state,
    and what to flag as conflict.

    Download: only_remote + differ-where-remote-is-newer, restricted to
    factors whose status is not DELETED / SUBMITTED. Tombstones and
    in-staging factors should never produce local data via pull.
    Conflict: differ where local is newer or tie — pull should not
    overwrite local edits that haven't been pushed.
    """
    to_download: list[str] = []
    skipped_state: list[str] = []
    conflicts: list[str] = []

    candidates = list(result.only_remote)
    for rel in result.differ:
        side = newer_side(rel, result)
        if side == "remote":
            candidates.append(rel)
        else:
            conflicts.append(rel)

    for rel in candidates:
        name = _extract_factor_name(rel, subdir)
        status = _factor_status(state, name)
        if status in ("DELETED", "SUBMITTED"):
            skipped_state.append(rel)
        else:
            to_download.append(rel)

    to_download.sort()
    skipped_state.sort()
    return to_download, skipped_state, conflicts


def _pull_dir(name: str, local_root: Path, remote_prefix: str,
              s3: S3Client, state: dict, *, library_id: str, dry_run: bool,
              recompute: bool = False) -> int:
    """Diff one data dir, download what's missing/remote-newer (filtering
    DELETED/SUBMITTED), warn on conflicts. Returns number of failed
    downloads."""
    cache = etag_cache.load(library_id)
    local = walk_local(local_root, subdir=name, cache=cache,
                       recompute=recompute)
    remote = list_remote(s3, remote_prefix)
    result = diff(local, remote)
    to_download, skipped_state, conflicts = _split_for_pull(result, name, state)
    info(f"  本地: {len(local)}  远端: {len(remote)}  "
         f"一致: {len(result.identical)}  待拉: {len(to_download)}  "
         f"跳过(DELETED/SUBMITTED): {len(skipped_state)}  "
         f"冲突: {len(conflicts)}" + ("  [recompute]" if recompute else ""))
    if conflicts:
        warn(f"  ⚠ {len(conflicts)} 个文件本地更新或 mtime 持平,跳过避免覆盖")
        for rel in conflicts[:5]:
            highlight(f"    ≠ {name}/{rel}")
        if len(conflicts) > 5:
            highlight(f"    … 另 {len(conflicts) - 5} 个")
    etag_cache.prune(cache, name, set(local.keys()))
    if not to_download or dry_run:
        etag_cache.save(library_id, cache)
        return 0
    local_root.mkdir(parents=True, exist_ok=True)

    updates: list[tuple[str, float, int, str]] = []
    updates_lock = threading.Lock()

    def _download_one(rel: str) -> str | None:
        try:
            ok = s3.download(f"{remote_prefix}/{rel}", local_root / rel)
            if not ok:
                return f"{rel}: 远端 key 404 (list 之后被删/被覆盖?)"
            ro = result.remote.get(rel)
            if ro is not None:
                try:
                    os.utime(local_root / rel, (ro.mtime, ro.mtime))
                except OSError:
                    pass
                if ro.etag:
                    with updates_lock:
                        updates.append((rel, ro.mtime, ro.size, ro.etag))
            return None
        except Exception as e:
            return f"{rel}: {e}"

    failed_count = 0
    progress = tqdm(total=len(to_download), desc=f"  {name}", unit="file")
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_download_one, rel): rel for rel in to_download}
        for fut in as_completed(futures):
            progress.update(1)
            err = fut.result()
            if err:
                failed_count += 1
                warn(f"    ✘ {err}")
    progress.close()

    for rel, mtime, size, etag in updates:
        etag_cache.put(cache, name, rel, mtime, size, etag)
    etag_cache.save(library_id, cache)
    return failed_count


def pull(config: Config, *, dry_run: bool = False, deep: bool = False) -> int:
    s3 = _make_s3(config)
    pfx = _pfx(config)
    library_id = config.library_id
    failed = 0

    banner("pull state (merge)")
    failed += _merge_states(config, s3, pfx, upload=False, dry_run=dry_run)

    state = _load_state(cache_path(library_id, "factor_state.json"))

    for d in DATA_DIRS:
        banner(f"pull {d}" + (" (recompute)" if deep else ""))
        failed += _pull_dir(d, getattr(config, d), f"{pfx}/{d}",
                            s3, state, library_id=library_id,
                            dry_run=dry_run, recompute=deep)
    return failed

# PLACEHOLDER_CHUNK_8


def status(config: Config) -> int:
    """Quick state-level diff between local and remote (no data scan).

    Reports four numbers: total local state, total remote state, names
    only on one side, and how many remote entries have a strictly newer
    `updated_at` than local (i.e. `behind`). For data-level discrepancy
    use `ops sync verify`.
    """
    s3 = _make_s3(config)
    pfx = _pfx(config)
    library_id = config.library_id
    local_state = _load_state(cache_path(library_id, "factor_state.json"))
    remote_state: dict = {}
    with tempfile.TemporaryDirectory(prefix="ops-status-") as td:
        tmp = Path(td) / "factor_state.json"
        ok = s3.download(f"{pfx}/.state/factor_state.json", tmp)
        if ok:
            remote_state = _load_state(tmp)
        else:
            warn("  ⚠ 远端 factor_state.json 不存在")
    local_only = set(local_state) - set(remote_state)
    remote_only = set(remote_state) - set(local_state)
    behind = _state_behind(local_state, remote_state)
    info(f"  本地 state:        {len(local_state)}")
    info(f"  远端 state:        {len(remote_state)}")
    info(f"  仅本地有:           {len(local_only)}")
    info(f"  仅远端有:           {len(remote_only)}")
    info(f"  远端比本地新:        {len(behind)}")
    if remote_only:
        highlight(f"  (pull): {', '.join(sorted(remote_only)[:5])}")
    if local_only:
        highlight(f"  (push): {', '.join(sorted(local_only)[:5])}")
    if behind:
        highlight(f"  (behind): {', '.join(sorted(behind)[:5])}")
    return 0


def verify(config: Config, *, deep: bool = False) -> int:
    """Real two-end inventory check.

    Lists alpha_src / alpha_pnl / alpha_feature on both ends and reports
    only_local / only_remote / etag-differ counts per dir. Read-only —
    never touches files or remote state.

    `deep=True` ignores the local etag cache and re-hashes every local
    file — use to catch corruption that may have happened in-place
    without updating mtime/size.

    Returns the total number of discrepancies (0 = perfectly in sync).
    """
    s3 = _make_s3(config)
    pfx = _pfx(config)
    library_id = config.library_id
    total_bad = 0
    for d in DATA_DIRS:
        banner(f"verify {d}" + (" (recompute)" if deep else ""))
        local_root: Path = getattr(config, d)
        cache = etag_cache.load(library_id)
        local = walk_local(local_root, subdir=d, cache=cache, recompute=deep)
        remote = list_remote(s3, f"{pfx}/{d}")
        result = diff(local, remote)
        etag_cache.prune(cache, d, set(local.keys()))
        etag_cache.save(library_id, cache)
        info(f"  本地: {len(local)}  远端: {len(remote)}  一致: {len(result.identical)}")
        if result.is_clean():
            info("  ✔ 完全一致")
            continue
        if result.only_local:
            warn(f"  仅本地 ({len(result.only_local)}):")
            for rel in result.only_local[:10]:
                highlight(f"    + {rel}")
            if len(result.only_local) > 10:
                highlight(f"    … 另 {len(result.only_local) - 10} 个")
        if result.only_remote:
            warn(f"  仅远端 ({len(result.only_remote)}):")
            for rel in result.only_remote[:10]:
                highlight(f"    - {rel}")
            if len(result.only_remote) > 10:
                highlight(f"    … 另 {len(result.only_remote) - 10} 个")
        if result.differ:
            warn(f"  内容不一致 ({len(result.differ)}):")
            for rel in result.differ[:10]:
                lo = result.local[rel]
                ro = result.remote[rel]
                side = newer_side(rel, result)
                highlight(f"    ≠ {rel}  local={lo.size}/{lo.etag[:8]}  "
                          f"remote={ro.size}/{ro.etag[:8]}  newer={side}")
            if len(result.differ) > 10:
                highlight(f"    … 另 {len(result.differ) - 10} 个")
        total_bad += (len(result.only_local) + len(result.only_remote)
                      + len(result.differ))
    return total_bad


def run_sync(args) -> None:
    config = Config.load(args.config_path)
    action: str = args.action
    deep: bool = getattr(args, "deep", False)
    if action in ("push", "pull"):
        if action == "push":
            failed = push(config, dry_run=args.dry_run,
                          force_state=getattr(args, "force_state", False),
                          deep=deep)
        else:
            failed = pull(config, dry_run=args.dry_run, deep=deep)
        banner(f"{action} 汇总")
        if failed:
            error(f"✘ 失败: {failed}")
        else:
            info("✔ 全部成功")
        bottom()
    elif action == "status":
        banner("sync status")
        status(config)
        bottom()
    elif action == "verify":
        failed = verify(config, deep=deep)
        banner("verify 汇总")
        if failed:
            warn(f"⚠ 不一致: {failed}")
        else:
            info("✔ 完全一致")
        bottom()
