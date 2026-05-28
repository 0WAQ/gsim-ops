"""rclone-based cross-server factor library sync.

Design: manifest-driven `rclone copy` (additive, never deletes) for data
+ per-record timestamp merge for the 3 state files.

Remote layout:

    <remote>/<library_id>/
    ├── alpha_src/
    ├── alpha_dump/              ← per-factor .tar.zst archives
    │   └── <name>.tar.zst
    ├── alpha_pnl/
    ├── alpha_feature/
    └── .state/
        ├── factor_state.json
        ├── metrics.json
        └── datasources.json

Local alpha_dump stays as per-date npy (gsim compatibility); only the
remote/transfer format uses tar.zst compression.

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
    "alpha_dump":    ["--transfers", "8", "--checksum"],
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


# ───────────────────────── tar.zst helpers ─────────────────────────────

def _check_zstd() -> None:
    try:
        import zstandard  # noqa: F401
    except ImportError:
        raise RuntimeError("zstandard 未安装 — uv add zstandard")


def _tar_zst_factor(name: str, dump_dir: Path, tmp_dir: Path) -> Path:
    """Tar + zstd compress a factor's dump directory."""
    import tarfile
    import zstandard as zstd

    archive = tmp_dir / f"{name}.tar.zst"
    factor_dir = dump_dir / name
    if not factor_dir.exists():
        raise FileNotFoundError(f"dump dir not found: {factor_dir}")

    cctx = zstd.ZstdCompressor(level=3, threads=-1)
    with open(archive, "wb") as fh:
        with cctx.stream_writer(fh) as compressor:
            with tarfile.open(fileobj=compressor, mode="w|") as tar:
                tar.add(str(factor_dir), arcname=name)
    return archive


def _untar_zst_factor(archive: Path, dump_dir: Path) -> None:
    """Extract a tar.zst archive into dump_dir."""
    import tarfile
    import zstandard as zstd

    dump_dir.mkdir(parents=True, exist_ok=True)
    dctx = zstd.ZstdDecompressor()
    with open(archive, "rb") as fh:
        with dctx.stream_reader(fh) as reader:
            with tarfile.open(fileobj=reader, mode="r|") as tar:
                tar.extractall(path=str(dump_dir))


def _push_dump_archives(changes: ChangeSet, config: Config,
                        remote_base: str, *, dry_run: bool) -> int:
    """Tar.zst each changed factor's dump dir and upload as a single archive."""
    from tqdm import tqdm

    names = sorted(changes.alpha_dump.keys())
    if not names:
        return 0
    info(f"  → alpha_dump: {len(names)} factors (tar.zst)")
    failed = 0
    with tempfile.TemporaryDirectory(prefix="ops-sync-dump-") as td:
        tmp_dir = Path(td)
        for name in tqdm(names, desc="  pack+push", unit="factor"):
            if dry_run:
                info(f"    [dry-run] tar.zst {name} → upload")
                continue
            try:
                archive = _tar_zst_factor(name, config.alpha_dump, tmp_dir)
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                error(f"    ✘ tar {name}: {e}")
                failed += 1
                continue
            rc, _ = _rclone(
                "copyto", str(archive),
                f"{remote_base}/alpha_dump/{name}.tar.zst",
                "--checksum",
                capture=True,
            )
            if rc != 0:
                error(f"    ✘ upload {name}.tar.zst rc={rc}")
                failed += 1
            archive.unlink(missing_ok=True)
    return failed


def _pull_dump_archives(names: list[str], config: Config,
                        remote_base: str, *, dry_run: bool) -> int:
    """Download and extract tar.zst archives for the given factor names."""
    from tqdm import tqdm

    if not names:
        return 0
    info(f"  → alpha_dump: {len(names)} factors (tar.zst)")
    failed = 0
    with tempfile.TemporaryDirectory(prefix="ops-sync-dump-") as td:
        tmp_dir = Path(td)
        for name in tqdm(names, desc="  pull+unpack", unit="factor"):
            archive = tmp_dir / f"{name}.tar.zst"
            rc, _ = _rclone(
                "copyto",
                f"{remote_base}/alpha_dump/{name}.tar.zst",
                str(archive),
                "--retries", "2",
                dry_run=dry_run, capture=True,
            )
            if rc != 0:
                warn(f"    ⚠ alpha_dump/{name}.tar.zst rc={rc}")
                failed += 1
                continue
            if not dry_run and archive.exists():
                try:
                    _untar_zst_factor(archive, config.alpha_dump)
                except subprocess.CalledProcessError as e:
                    error(f"    ✘ untar {name}: {e}")
                    failed += 1
                archive.unlink(missing_ok=True)
    return failed


def _list_remote_dump_archives(config: Config, remote_base: str) -> list[str]:
    """List factor names that have .tar.zst archives on remote."""
    rc, stdout = _rclone(
        "lsf", f"{remote_base}/alpha_dump/",
        "--include", "*.tar.zst",
        capture=True,
    )
    if rc != 0:
        return []
    names = []
    for line in stdout.strip().splitlines():
        line = line.strip()
        if line.endswith(".tar.zst"):
            names.append(line[:-len(".tar.zst")])
    return names


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


def _force_push_states(config: Config, *, dry_run: bool) -> int:
    """Upload local state files directly to overwrite remote. No merge."""
    library_id = config.library_id
    remote_state = f"{_remote_base(config)}/.state"
    failed = 0
    for fname in STATE_FILES:
        local = cache_path(library_id, fname)
        if not local.exists():
            info(f"  · {fname}: 本地不存在,跳过")
            continue
        rc, _ = _rclone(
            "copyto", str(local), f"{remote_state}/{fname}",
            dry_run=dry_run, capture=True,
        )
        if rc != 0:
            error(f"  ✘ {fname} 上传失败 rc={rc}")
            failed += 1
        else:
            info(f"  ✔ {fname} 已直接上传(force-state)")
    return failed


# ───────────────────────── manifest helpers ───────────────────────────

def _ensure_manifest(config: Config) -> None:
    """Rebuild manifest if missing or stale (covers fewer factors than disk)."""
    library_id = config.library_id
    names = list_factor_names(config)
    if not names:
        return
    existing = load_manifest(library_id)
    if existing is not None and len(existing.factors) >= len(names):
        return
    manifest = SyncManifest(factors={
        name: stat_factor(name, config) for name in names
    })
    save_manifest(library_id, manifest)
    if existing is None:
        info(f"  ✔ manifest 新建 ({len(manifest.factors)} factors)")
    else:
        info(f"  ✔ manifest 重建 ({len(existing.factors)} → {len(manifest.factors)} factors)")


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

def push(config: Config, *, dry_run: bool = False, force_state: bool = False) -> int:
    """Push local data + state to remote.

    Checks remote state first: if remote has factors not in local state,
    refuse and ask user to pull first (like git push refusing when behind).
    No manifest → treat as empty (everything looks new). Manifest is
    written only after a successful data push.

    --force-state skips pre-push check + merge, uploading local state
    files directly to overwrite remote.
    """
    _check_rclone()
    _check_zstd()
    _require_remote(config)

    failed = 0
    remote_base = _remote_base(config)
    library_id = config.library_id

    if not force_state:
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
                warn("  → 或 ops sync push --force-state 用本地 state 覆盖远端")
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
            if d == "alpha_dump":
                continue
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
        failed += _push_dump_archives(changes, config, remote_base, dry_run=dry_run)
        if failed == 0 and not dry_run:
            for name, fp in fresh.items():
                manifest.factors[name] = fp
            save_manifest(library_id, manifest)
            info("  ✔ manifest 已更新")

    banner("push state" + (" (force overwrite)" if force_state else " (merge)"))
    if force_state:
        failed += _force_push_states(config, dry_run=dry_run)
    else:
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
    _check_zstd()
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
            if d == "alpha_dump":
                continue
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
        all_dump_names = _list_remote_dump_archives(config, remote_base)
        failed += _pull_dump_archives(all_dump_names, config, remote_base, dry_run=dry_run)
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

                    failed += _pull_dump_archives(missing, config, remote_base, dry_run=dry_run)

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
    """Full reconciliation via rclone check on each data dir.
    For alpha_dump, verifies that each local factor has a matching .tar.zst on remote."""
    _check_rclone()
    _require_remote(config)
    remote_base = _remote_base(config)
    failed = 0
    for d in DATA_DIRS:
        if d == "alpha_dump":
            continue
        banner(f"verify {d}")
        local = getattr(config, d)
        rc, _ = _rclone("check", str(local), f"{remote_base}/{d}",
                        *DATA_FLAGS.get(d, []), "--one-way")
        if rc != 0:
            warn(f"  ⚠ {d} 存在差异 rc={rc}")
            failed += 1
        else:
            info(f"  ✔ {d} 一致")
    banner("verify alpha_dump (archives)")
    remote_archives = set(_list_remote_dump_archives(config, remote_base))
    local_dump_names: set[str] = set()
    if config.alpha_dump.exists():
        with os.scandir(config.alpha_dump) as it:
            for entry in it:
                if entry.is_dir() and not entry.name.startswith("."):
                    local_dump_names.add(entry.name)
    missing_remote = local_dump_names - remote_archives
    if missing_remote:
        warn(f"  ⚠ {len(missing_remote)} factors 本地有 dump 但远端无 archive")
        for name in sorted(missing_remote)[:5]:
            warn(f"    · {name}")
        failed += 1
    else:
        info(f"  ✔ alpha_dump: {len(local_dump_names)} factors 均有远端 archive")
    banner("verify .state")
    rc, _ = _rclone("check", str(library_cache_dir(config.library_id)),
                    f"{remote_base}/.state",
                    *[a for fn in STATE_FILES for a in ("--include", fn)],
                    "--one-way")
    if rc != 0:
        warn(f"  ⚠ .state 存在差异 rc={rc}")
        failed += 1
    return failed


def rebuild(config: Config) -> int:
    """Unconditionally rebuild the local sync manifest from disk."""
    import time as _time
    library_id = config.library_id
    t0 = _time.time()
    names = list_factor_names(config)
    if not names:
        warn("  本地无因子,无需重建")
        return 0
    manifest = SyncManifest(factors={
        name: stat_factor(name, config) for name in names
    })
    save_manifest(library_id, manifest)
    elapsed = _time.time() - t0
    info(f"  ✔ manifest 已重建: {len(names)} 因子, 耗时 {elapsed:.1f}s")
    return 0


def run_sync(args) -> None:
    config: Config = Config.load(args.config_path)
    action: str = args.action

    if action in ("push", "pull"):
        if action == "push":
            failed = push(config, dry_run=args.dry_run, force_state=getattr(args, "force_state", False))
        elif action == "pull":
            failed = pull(config, dry_run=args.dry_run)
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
    elif action == "rebuild":
        banner("sync rebuild")
        rebuild(config)
        bottom()
    else:
        raise ValueError(f"unknown action: {action}")
