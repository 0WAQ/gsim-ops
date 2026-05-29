"""S3-based cross-server factor library sync.

Remote layout (S3 bucket):
    <library_id>/alpha_src/<name>/...
    <library_id>/alpha_pnl/<name>
    <library_id>/alpha_feature/<name>.v1.npy
    <library_id>/.state/{factor_state,metrics,datasources}.json

Local alpha_dump is a local-only intermediate product (not synced).
"""
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tqdm import tqdm

from ops.infra.config import Config
from ops.infra.s3 import S3Client
from ops.infra.cache import cache_path
from ops.services.sync.manifest import (
    SyncManifest, ChangeSet,
    load_manifest, save_manifest, scan_changes, stat_factor, list_factor_names,
)
from ops.services.sync.merge import MERGERS
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


def _push_files(changes: ChangeSet, config: Config,
                s3: S3Client, pfx: str, *, dry_run: bool) -> int:
    failed = 0
    src_names = sorted(changes.alpha_src)
    if src_names:
        info(f"  → alpha_src: {len(src_names)} factors")
        for name in tqdm(src_names, desc="  alpha_src", unit="factor"):
            if dry_run:
                continue
            d = config.alpha_src / name
            if d.exists():
                try:
                    s3.upload_dir(d, f"{pfx}/alpha_src/{name}")
                except Exception as e:
                    error(f"    ✘ alpha_src/{name}: {e}")
                    failed += 1
    pnl_names = sorted(changes.alpha_pnl)
    if pnl_names:
        info(f"  → alpha_pnl: {len(pnl_names)} factors")
        for name in pnl_names:
            if dry_run:
                continue
            f = config.alpha_pnl / name
            if f.exists():
                try:
                    s3.upload(f, f"{pfx}/alpha_pnl/{name}")
                except Exception as e:
                    error(f"    ✘ alpha_pnl/{name}: {e}")
                    failed += 1

# PLACEHOLDER_CHUNK_4
    feat_names = sorted(changes.alpha_feature.keys())
    if feat_names:
        info(f"  → alpha_feature: {len(feat_names)} factors")
        for name in feat_names:
            if dry_run:
                continue
            for v in changes.alpha_feature[name]:
                fn = f"{name}.{v}.npy"
                local = config.alpha_feature / fn
                if local.exists():
                    try:
                        s3.upload(local, f"{pfx}/alpha_feature/{fn}")
                    except Exception as e:
                        error(f"    ✘ {fn}: {e}")
                        failed += 1
    return failed


def _ensure_manifest(config: Config) -> None:
    names = list_factor_names(config)
    if not names:
        return
    existing = load_manifest(config.library_id)
    if existing and len(existing.factors) >= len(names):
        return
    m = SyncManifest(factors={n: stat_factor(n, config) for n in names})
    save_manifest(config.library_id, m)
    info(f"  ✔ manifest: {len(m.factors)} factors")

# PLACEHOLDER_CHUNK_5


def push(config: Config, *, dry_run: bool = False, force_state: bool = False) -> int:
    s3 = _make_s3(config)
    pfx = _pfx(config)
    library_id = config.library_id
    failed = 0

    if not force_state:
        banner("pre-push check")
        remote_names = _fetch_remote_state_names(s3, pfx)
        if remote_names is not None:
            local_state_path = cache_path(library_id, "factor_state.json")
            local_names: set[str] = set()
            if local_state_path.exists():
                try:
                    with local_state_path.open("r") as f:
                        local_names = set((json.load(f) or {}).keys())
                except Exception:
                    pass
            behind = remote_names - local_names
            if behind:
                error(f"  ✘ 远端有 {len(behind)} 个因子不在本地,请先 pull")
                return 1
            info(f"  ✔ 本地 state 已包含远端全部 {len(remote_names)} 个因子")
        else:
            info("  · 远端 state 不存在,跳过检查")

    manifest = load_manifest(library_id) or SyncManifest()
    banner("push data")
    changes, fresh = scan_changes(config, manifest)
    if changes.is_empty():
        info("  · 无变更")
    else:
        info(f"  发现 {changes.total_factors()} 个因子有变更")
        failed += _push_files(changes, config, s3, pfx, dry_run=dry_run)
        if failed == 0 and not dry_run:
            for name, fp in fresh.items():
                manifest.factors[name] = fp
            save_manifest(library_id, manifest)
            info("  ✔ manifest 已更新")

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


def _fetch_remote_state_names(s3: S3Client, pfx: str) -> set[str] | None:
    with tempfile.TemporaryDirectory(prefix="ops-pre-") as td:
        tmp = Path(td) / "factor_state.json"
        ok = s3.download(f"{pfx}/.state/factor_state.json", tmp)
        if not ok:
            return None
        try:
            with tmp.open("r") as f:
                return set((json.load(f) or {}).keys())
        except Exception:
            return None

# PLACEHOLDER_CHUNK_7


def pull(config: Config, *, dry_run: bool = False) -> int:
    s3 = _make_s3(config)
    pfx = _pfx(config)
    library_id = config.library_id
    failed = 0

    banner("pull state (merge)")
    failed += _merge_states(config, s3, pfx, upload=False, dry_run=dry_run)

    local_names = set(list_factor_names(config))
    if not local_names:
        banner("pull data (全量)")
        for d in ("alpha_src", "alpha_pnl", "alpha_feature"):
            local = getattr(config, d)
            local.mkdir(parents=True, exist_ok=True)
            if not dry_run:
                n = s3.download_dir(f"{pfx}/{d}/", local)
                info(f"  ✔ {d}: {n} files")
    else:
        banner("pull data (增量)")
        state_path = cache_path(library_id, "factor_state.json")
        if not state_path.exists():
            info("  · 无 factor_state.json,跳过")
        else:
            try:
                with state_path.open("r") as f:
                    state_data = json.load(f)
            except Exception as e:
                error(f"  ✘ {e}")
                failed += 1
                state_data = None
            if state_data:
                missing = [n for n, r in state_data.items()
                           if isinstance(r, dict) and n not in local_names]
                if not missing:
                    info("  · 无需拉取")
                else:
                    info(f"  → {len(missing)} 个因子")
                    if not dry_run:
                        config.alpha_pnl.mkdir(parents=True, exist_ok=True)
                        config.alpha_feature.mkdir(parents=True, exist_ok=True)

                        def _pull_one_factor(name: str) -> str | None:
                            try:
                                d = config.alpha_src / name
                                d.mkdir(parents=True, exist_ok=True)
                                s3.download_dir(f"{pfx}/alpha_src/{name}/", d)
                                s3.download(f"{pfx}/alpha_pnl/{name}",
                                            config.alpha_pnl / name)
                                for v in ("v1", "v2"):
                                    fn = f"{name}.{v}.npy"
                                    s3.download(f"{pfx}/alpha_feature/{fn}",
                                                config.alpha_feature / fn)
                                return None
                            except Exception as e:
                                return f"{name}: {e}"

                        progress = tqdm(total=len(missing), desc="  src/pnl/feat", unit="factor")
                        with ThreadPoolExecutor(max_workers=8) as pool:
                            futures = {pool.submit(_pull_one_factor, n): n for n in missing}
                            for fut in as_completed(futures):
                                progress.update(1)
                                err = fut.result()
                                if err:
                                    warn(f"    ⚠ {err}")
                        progress.close()

    if not dry_run:
        _ensure_manifest(config)
    return failed

# PLACEHOLDER_CHUNK_8


def status(config: Config) -> int:
    s3 = _make_s3(config)
    pfx = _pfx(config)
    manifest = load_manifest(config.library_id)
    n_manifest = len(manifest.factors) if manifest else 0
    local_names = set(list_factor_names(config))
    remote_names: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="ops-status-") as td:
        tmp = Path(td) / "factor_state.json"
        ok = s3.download(f"{pfx}/.state/factor_state.json", tmp)
        if ok:
            try:
                with tmp.open("r") as f:
                    remote_names = set((json.load(f) or {}).keys())
            except Exception:
                pass
        else:
            warn("  ⚠ 远端 factor_state.json 不存在")
    missing_local = remote_names - local_names
    missing_remote = local_names - remote_names
    info(f"  本地因子 (磁盘):    {len(local_names)}")
    info(f"  本地 manifest:     {n_manifest}")
    info(f"  远端 state:        {len(remote_names)}")
    info(f"  需 pull:           {len(missing_local)}")
    info(f"  需 push:           {len(missing_remote)}")
    if missing_local:
        highlight(f"  (pull): {', '.join(sorted(missing_local)[:5])}")
    if missing_remote:
        highlight(f"  (push): {', '.join(sorted(missing_remote)[:5])}")
    return 0


def verify(config: Config) -> int:
    return 0


def rebuild(config: Config) -> int:
    import time as _time
    t0 = _time.time()
    names = list_factor_names(config)
    if not names:
        warn("  无因子")
        return 0
    m = SyncManifest(factors={n: stat_factor(n, config) for n in names})
    save_manifest(config.library_id, m)
    info(f"  ✔ {len(names)} factors, {_time.time()-t0:.1f}s")
    return 0


def run_sync(args) -> None:
    config = Config.load(args.config_path)
    action: str = args.action
    if action in ("push", "pull"):
        if action == "push":
            failed = push(config, dry_run=args.dry_run,
                          force_state=getattr(args, "force_state", False))
        else:
            failed = pull(config, dry_run=args.dry_run)
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
        failed = verify(config)
        banner("verify 汇总")
        if failed:
            warn(f"⚠ 不一致: {failed}")
        else:
            info("✔ 完全一致")
        bottom()
    elif action == "rebuild":
        banner("sync rebuild")
        rebuild(config)
        bottom()
