"""rclone-based cross-server factor library sync.

Design: manifest-driven `rclone copy` (additive, never deletes) for data
+ per-record timestamp merge for the 3 state files.

Remote layout (option A — state hidden under data root):

    <remote>/<library_id>/
    ├── alpha_src/
    ├── alpha_dump/
    ├── alpha_pnl/
    ├── alpha_feature/
    └── .state/
        ├── factor_state.json
        ├── metrics.json
        └── datasources.json

Local state lives at ~/.cache/ops/lib/<library_id>/; per-machine fcntl
locks at ~/.cache/ops/locks/ are NEVER synced.
"""
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ops.infra.config import Config
from ops.infra.cache import library_cache_dir, cache_path
from ops.services.sync.manifest import (
    SyncManifest,
    ChangeSet,
    FactorFingerprint,
    FEATURE_VERSIONS,
    load_manifest,
    save_manifest,
    scan_changes,
    stat_factor,
    list_factor_names,
)
from ops.services.sync.merge import MERGERS
from ops.utils.logger.log import banner, bottom, info, warn, error, highlight


# Files inside ~/.cache/ops/lib/<library_id>/ that get synced.
# index.json + sync_manifest.json are intentionally NOT synced.
STATE_FILES = ("factor_state.json", "metrics.json", "datasources.json")

DATA_DIRS = ("alpha_src", "alpha_dump", "alpha_pnl", "alpha_feature")
DATA_FLAGS: dict[str, list[str]] = {
    "alpha_dump":    ["--transfers", "32", "--checkers", "32"],
    "alpha_feature": ["--transfers", "8",  "--checksum"],
    "alpha_src":     [],
    "alpha_pnl":     [],
}


# ───────────────────────── rclone wrappers ──────────────────────────────

def _check_rclone() -> None:
    if shutil.which("rclone") is None:
        raise RuntimeError("rclone 未安装或不在 PATH 中")


def _rclone(*args: str, dry_run: bool = False, capture: bool = False) -> tuple[int, str]:
    cmd = ["rclone", *args]
    if not capture:
        cmd += ["--progress", "--stats-one-line"]
    if dry_run:
        cmd.append("--dry-run")
    info(f"  $ {' '.join(cmd)}")
    if capture:
        r = subprocess.run(cmd, check=False, capture_output=True, text=True)
        return r.returncode, r.stdout
    return subprocess.run(cmd, check=False).returncode, ""


def _require_remote(config: Config) -> None:
    if not config.sync_remote:
        raise RuntimeError(
            "config 中未配置 sync.remote — 在 config.yaml 加入:\n"
            "  sync:\n    remote: <rclone-remote>:<bucket>"
        )


def _remote_base(config: Config) -> str:
    return f"{config.sync_remote}/{config.library_id}"


# ───────────────────────── files_from build ─────────────────────────────

def _build_files_from(changes: ChangeSet, kind: str, config: Config
                      ) -> tuple[list[str], int]:
    """Return (list of paths relative to <kind> dir, count of files).

    Layout:
      alpha_src/<name>/...           — push whole dir
      alpha_dump/<name>/<YYYYMMDD>/* — push only new date dirs
      alpha_pnl/<name>               — single file at the top level
      alpha_feature/<name>.v{1,2}.npy — files at the top level
    """
    paths: list[str] = []
    if kind == "alpha_src":
        for name in sorted(changes.alpha_src):
            paths.extend(_list_files(config.alpha_src / name, prefix=name))
    elif kind == "alpha_pnl":
        for name in sorted(changes.alpha_pnl):
            if (config.alpha_pnl / name).exists():
                paths.append(name)
    elif kind == "alpha_dump":
        for name, dates in sorted(changes.alpha_dump.items()):
            for d in dates:
                paths.extend(_list_files(config.alpha_dump / name / d,
                                          prefix=f"{name}/{d}"))
    elif kind == "alpha_feature":
        for name, versions in sorted(changes.alpha_feature.items()):
            for v in versions:
                fname = f"{name}.{v}.npy"
                if (config.alpha_feature / fname).exists():
                    paths.append(fname)
    return paths, len(paths)


def _list_files(root: Path, *, prefix: str) -> list[str]:
    """List relative paths of files under root, prefixed with `prefix`."""
    out: list[str] = []
    if not root.exists():
        return out
    for dirpath, _dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        for n in filenames:
            if rel == ".":
                out.append(f"{prefix}/{n}")
            else:
                out.append(f"{prefix}/{rel}/{n}")
    return out


def _write_files_from(paths: list[str]) -> Path:
    fd, tmp = tempfile.mkstemp(prefix="ops-sync-files-", suffix=".txt", text=True)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for p in paths:
            f.write(p + "\n")
    return Path(tmp)


# ───────────────────────── data push / pull ─────────────────────────────

def _copy_with_files_from(local: Path, remote: str, paths: list[str],
                          flags: list[str], *, direction: str, dry_run: bool) -> int:
    if not paths:
        return 0
    files_from = _write_files_from(paths)
    try:
        if direction == "push":
            src, dst = str(local), remote
        else:
            src, dst = remote, str(local)
        rc, _ = _rclone(
            "copy", src, dst,
            "--files-from", str(files_from),
            "--no-traverse",
            *flags,
            dry_run=dry_run,
        )
        return rc
    finally:
        try:
            files_from.unlink()
        except OSError:
            pass


def _bootstrap_copy_dir(local: Path, remote: str, flags: list[str],
                        *, direction: str, dry_run: bool) -> int:
    """Bootstrap: copy a whole dir end-to-end (still additive)."""
    if direction == "push":
        if not local.exists():
            warn(f"  ⚠ 本地不存在 {local},跳过")
            return 0
        src, dst = str(local), remote
    else:
        src, dst = remote, str(local)
        local.mkdir(parents=True, exist_ok=True)
    rc, _ = _rclone("copy", src, dst, *flags, dry_run=dry_run)
    return rc


# ───────────────────────── state merge round-trip ───────────────────────

def _merge_states(config: Config, *, upload: bool, dry_run: bool) -> int:
    """For each of the 3 state files:
       1. download remote → tmp (rclone copyto)
       2. merge tmp into local
       3. (optional) upload merged local → remote

    Returns number of failed file mergers."""
    library_id = config.library_id
    remote_state = f"{_remote_base(config)}/.state"
    failed = 0
    for fname in STATE_FILES:
        local = cache_path(library_id, fname)
        # 1. download — copyto preserves filename
        with tempfile.TemporaryDirectory(prefix="ops-sync-state-") as td:
            tmp_remote = Path(td) / fname
            rc, _ = _rclone(
                "copyto", f"{remote_state}/{fname}", str(tmp_remote),
                "--ignore-existing", "--retries", "1",
                capture=True,
            )
            # rc != 0 usually means "remote file absent" — first push case.
            if rc != 0 or not tmp_remote.exists():
                info(f"  · {fname}: 远端不存在,跳过 merge")
                remote_present = False
            else:
                remote_present = True
                try:
                    merger = MERGERS[fname]
                    added, updated = merger(local, tmp_remote)
                    info(f"  ✔ {fname} merge: +{added} added, {updated} updated")
                except Exception as e:
                    error(f"  ✘ {fname} merge 失败: {e}")
                    failed += 1
                    continue

            # 2. upload merged local back (push only)
            if upload and local.exists():
                rc2, _ = _rclone(
                    "copyto", str(local), f"{remote_state}/{fname}",
                    dry_run=dry_run, capture=True,
                )
                if rc2 != 0:
                    error(f"  ✘ {fname} 上传失败 rc={rc2}")
                    failed += 1
                else:
                    info(f"  ✔ {fname} 已上传")
            elif not upload and not remote_present:
                # pull + no remote file: nothing to do
                pass
    return failed


# ───────────────────────── manifest helpers ───────────────────────────

def _ensure_manifest(config: Config) -> None:
    """If no manifest exists, build one from disk so subsequent push is
    incremental. Idempotent — skips if manifest already present."""
    library_id = config.library_id
    if load_manifest(library_id) is not None:
        return
    names = list_factor_names(config)
    if not names:
        return
    manifest = SyncManifest(factors={
        name: stat_factor(name, config) for name in names
    })
    save_manifest(library_id, manifest)
    info(f"  ✔ manifest 重建 ({len(manifest.factors)} factors)")


# ───────────────────────── pre-push check ─────────────────────────────

def _fetch_remote_state_names(config: Config) -> set[str] | None:
    """Download remote factor_state.json and return its factor names.
    Returns None if remote is unreachable or empty."""
    remote_state = f"{_remote_base(config)}/.state/factor_state.json"
    with tempfile.TemporaryDirectory(prefix="ops-sync-pre-") as td:
        tmp = Path(td) / "factor_state.json"
        rc, _ = _rclone("copyto", remote_state, str(tmp),
                        "--retries", "1", capture=True)
        if rc != 0 or not tmp.exists():
            return None
        try:
            with tmp.open("r", encoding="utf-8") as f:
                data = json.load(f) or {}
            return set(data.keys())
        except Exception:
            return None


# ───────────────────────── public entry points ──────────────────────────

def push(config: Config, *, dry_run: bool = False) -> int:
    """Push local data + state to remote.

    Checks remote state first: if remote has factors not in local state,
    refuse and ask user to pull first (like git push refusing when behind).
    No manifest → treat as empty (everything looks new). Manifest is
    written only after a successful data push.
    """
    _check_rclone()
    _require_remote(config)

    failed = 0
    remote_base = _remote_base(config)
    library_id = config.library_id

    banner("pre-push check")
    remote_names = _fetch_remote_state_names(config)
    if remote_names is not None:
        local_state_path = cache_path(library_id, "factor_state.json")
        local_names: set[str] = set()
        if local_state_path.exists():
            try:
                with local_state_path.open("r", encoding="utf-8") as f:
                    local_names = set((json.load(f) or {}).keys())
            except Exception:
                pass
        behind = remote_names - local_names
        if behind:
            error(f"  ✘ 远端有 {len(behind)} 个因子本地 state 中不存在,请先 pull")
            sample = sorted(behind)[:5]
            warn(f"  (示例): {', '.join(sample)}")
            warn("  → 运行 ops sync pull 后再 push")
            return 1
        info(f"  ✔ 本地 state 已包含远端全部 {len(remote_names)} 个因子")
    else:
        info("  · 远端 state 不存在或不可达,跳过检查")

    manifest = load_manifest(library_id) or SyncManifest()

    banner("push data")
    changes, fresh = scan_changes(config, manifest)
    if changes.is_empty():
        info("  · 无变更")
    else:
        info(f"  发现 {changes.total_factors()} 个因子有变更")
        for d in DATA_DIRS:
            paths, n = _build_files_from(changes, d, config)
            if not paths:
                continue
            info(f"  → {d}: {n} files")
            rc = _copy_with_files_from(
                getattr(config, d), f"{remote_base}/{d}",
                paths, DATA_FLAGS.get(d, []),
                direction="push", dry_run=dry_run,
            )
            if rc != 0:
                error(f"  ✘ {d} rc={rc}")
                failed += 1
        if failed == 0 and not dry_run:
            for name, fp in fresh.items():
                manifest.factors[name] = fp
            save_manifest(library_id, manifest)
            info("  ✔ manifest 已更新")

    banner("push state (merge)")
    failed += _merge_states(config, upload=True, dry_run=dry_run)
    return failed


def pull(config: Config, *, dry_run: bool = False) -> int:
    """Pull state + missing factors from remote.

    Auto-detects empty-local: if no factors exist locally, do a full
    `rclone copy` of every data dir. Otherwise: merge state, then pull
    only the factors named in remote `factor_state.json` that we lack.
    Always ensures a manifest exists after pull (rebuilds from disk if missing).
    """
    _check_rclone()
    _require_remote(config)

    failed = 0
    remote_base = _remote_base(config)
    library_id = config.library_id

    banner("pull state (merge)")
    failed += _merge_states(config, upload=False, dry_run=dry_run)

    local_names = set(list_factor_names(config))
    if not local_names:
        banner("pull data (空盘,全量拉)")
        for d in DATA_DIRS:
            local = getattr(config, d)
            rc = _bootstrap_copy_dir(
                local, f"{remote_base}/{d}", DATA_FLAGS.get(d, []),
                direction="pull", dry_run=dry_run,
            )
            if rc != 0:
                error(f"  ✘ {d} rc={rc}")
                failed += 1
            else:
                info(f"  ✔ {d}")
    else:
        banner("pull data (增量)")
        state_path = cache_path(library_id, "factor_state.json")
        if not state_path.exists():
            info("  · 本地 factor_state.json 不存在,跳过 data 拉取")
        else:
            try:
                with state_path.open("r", encoding="utf-8") as f:
                    state_data = json.load(f)
            except Exception as e:
                error(f"  ✘ 读取 factor_state.json 失败: {e}")
                failed += 1
                state_data = None

            if state_data is not None:
                missing = [
                    name for name, rec in state_data.items()
                    if isinstance(rec, dict) and name not in local_names
                ]
                if not missing:
                    info("  · 无需拉取")
                else:
                    info(f"  → 需要拉取 {len(missing)} 个因子")
                    for name in missing:
                        src = f"{remote_base}/alpha_src/{name}"
                        dst = str(config.alpha_src / name)
                        Path(dst).mkdir(parents=True, exist_ok=True)
                        rc, _ = _rclone("copy", src, dst,
                                        *DATA_FLAGS.get("alpha_src", []),
                                        dry_run=dry_run)
                        if rc != 0:
                            warn(f"  ⚠ alpha_src/{name} rc={rc}")

                        src = f"{remote_base}/alpha_dump/{name}"
                        dst = str(config.alpha_dump / name)
                        Path(dst).mkdir(parents=True, exist_ok=True)
                        rc, _ = _rclone("copy", src, dst,
                                        *DATA_FLAGS.get("alpha_dump", []),
                                        dry_run=dry_run)
                        if rc != 0:
                            warn(f"  ⚠ alpha_dump/{name} rc={rc}")

                        config.alpha_pnl.mkdir(parents=True, exist_ok=True)
                        rc, _ = _rclone(
                            "copyto",
                            f"{remote_base}/alpha_pnl/{name}",
                            str(config.alpha_pnl / name),
                            "--ignore-existing", "--retries", "1",
                            dry_run=dry_run, capture=True,
                        )
                        if rc != 0:
                            warn(f"  ⚠ alpha_pnl/{name} rc={rc}")

                        config.alpha_feature.mkdir(parents=True, exist_ok=True)
                        for v in ("v1", "v2"):
                            fname = f"{name}.{v}.npy"
                            rc, _ = _rclone(
                                "copyto",
                                f"{remote_base}/alpha_feature/{fname}",
                                str(config.alpha_feature / fname),
                                "--ignore-existing", "--retries", "1",
                                dry_run=dry_run, capture=True,
                            )
                            if rc != 0:
                                warn(f"  ⚠ alpha_feature/{fname} rc={rc}")

    if not dry_run:
        _ensure_manifest(config)
    return failed


def status(config: Config) -> int:
    """Cheap diff using local manifest + remote factor_state.json. No data scan."""
    _check_rclone()
    _require_remote(config)
    library_id = config.library_id

    manifest = load_manifest(library_id)
    n_local_manifest = len(manifest.factors) if manifest else 0
    local_names = set(list_factor_names(config))

    # fetch remote factor_state.json
    remote_state = f"{_remote_base(config)}/.state/factor_state.json"
    remote_records: dict = {}
    with tempfile.TemporaryDirectory(prefix="ops-sync-status-") as td:
        tmp = Path(td) / "factor_state.json"
        rc, _ = _rclone("copyto", remote_state, str(tmp),
                        "--retries", "1", capture=True)
        if rc == 0 and tmp.exists():
            try:
                with tmp.open("r", encoding="utf-8") as f:
                    remote_records = json.load(f) or {}
            except Exception:
                pass
        else:
            warn("  ⚠ 远端 factor_state.json 不可达或不存在")

    remote_names = set(remote_records.keys())
    missing_locally = remote_names - local_names
    missing_remotely = local_names - remote_names

    info(f"  本地因子数 (磁盘):    {len(local_names)}")
    info(f"  本地 manifest 因子数: {n_local_manifest}")
    info(f"  远端 state 因子数:    {len(remote_names)}")
    info(f"  远端有 / 本地无:      {len(missing_locally)}")
    info(f"  本地有 / 远端无:      {len(missing_remotely)}")
    if missing_locally:
        sample = sorted(missing_locally)[:5]
        highlight(f"  (示例-需 pull):     {', '.join(sample)}")
    if missing_remotely:
        sample = sorted(missing_remotely)[:5]
        highlight(f"  (示例-需 push):     {', '.join(sample)}")
    return 0


def verify(config: Config) -> int:
    """Full reconciliation via rclone check on each data dir."""
    _check_rclone()
    _require_remote(config)
    remote_base = _remote_base(config)
    failed = 0
    for d in DATA_DIRS:
        banner(f"verify {d}")
        local = getattr(config, d)
        rc, _ = _rclone("check", str(local), f"{remote_base}/{d}",
                        *DATA_FLAGS.get(d, []), "--one-way")
        if rc != 0:
            warn(f"  ⚠ {d} 存在差异 rc={rc}")
            failed += 1
        else:
            info(f"  ✔ {d} 一致")
    banner("verify .state")
    rc, _ = _rclone("check", str(library_cache_dir(config.library_id)),
                    f"{remote_base}/.state",
                    *[a for fn in STATE_FILES for a in ("--include", fn)],
                    "--one-way")
    if rc != 0:
        warn(f"  ⚠ .state 存在差异 rc={rc}")
        failed += 1
    return failed


def run_sync(args) -> None:
    config: Config = Config.load(args.config_path)
    action: str = args.action

    if action in ("push", "pull"):
        fn = push if action == "push" else pull
        failed = fn(config, dry_run=args.dry_run)
        banner(f"{action} 汇总")
        if failed:
            error(f"✘ 失败子任务: {failed}")
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
            warn(f"⚠ 不一致子任务: {failed}")
        else:
            info("✔ 完全一致")
        bottom()
    else:
        raise ValueError(f"unknown action: {action}")
