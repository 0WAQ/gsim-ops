"""rclone-based cross-server factor library sync.

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
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ops.infra.config import Config
from ops.infra.cache import library_cache_dir
from ops.utils.logger.log import banner, bottom, info, warn, error, highlight


# Files inside ~/.cache/ops/lib/<library_id>/ that get synced.
# index.json is intentionally NOT synced (1h TTL, cheap to regenerate).
STATE_FILES = ("factor_state.json", "metrics.json", "datasources.json")

# Per-subdir tuning.
DATA_DIRS = ("alpha_src", "alpha_dump", "alpha_pnl", "alpha_feature")
DATA_FLAGS: dict[str, list[str]] = {
    "alpha_dump":    ["--transfers", "32", "--checkers", "32", "--fast-list"],
    "alpha_feature": ["--transfers", "8",  "--checksum"],
    "alpha_src":     [],
    "alpha_pnl":     [],
}


@dataclass
class SyncTarget:
    label: str
    local: Path
    remote: str
    flags: list[str]


def _check_rclone() -> None:
    if shutil.which("rclone") is None:
        raise RuntimeError("rclone 未安装或不在 PATH 中")


def _rclone(*args: str, dry_run: bool = False) -> int:
    cmd = ["rclone", *args, "--progress", "--stats-one-line"]
    if dry_run:
        cmd.append("--dry-run")
    info(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, check=False).returncode


def _data_targets(config: Config) -> list[SyncTarget]:
    base = f"{config.sync_remote}/{config.library_id}"
    return [
        SyncTarget(
            label=name,
            local=getattr(config, name),
            remote=f"{base}/{name}",
            flags=DATA_FLAGS.get(name, []),
        )
        for name in DATA_DIRS
    ]


def _state_target(config: Config) -> SyncTarget:
    base = f"{config.sync_remote}/{config.library_id}"
    return SyncTarget(
        label=".state",
        local=library_cache_dir(config.library_id),
        remote=f"{base}/.state",
        # only ship the 3 synced json files; skip index.json + anything else
        flags=[a for f in STATE_FILES for a in ("--include", f)],
    )


def _run_targets(targets: list[SyncTarget], direction: str, dry_run: bool) -> int:
    """direction: 'push' (local→remote) or 'pull' (remote→local)."""
    failed = 0
    for t in targets:
        banner(f"{direction} {t.label}")
        if direction == "push":
            src, dst = str(t.local), t.remote
        else:
            src, dst = t.remote, str(t.local)
        rc = _rclone("sync", src, dst, *t.flags, dry_run=dry_run)
        if rc != 0:
            error(f"  ✘ {t.label} rclone exit={rc}")
            failed += 1
        else:
            info(f"  ✔ {t.label}")
    return failed


def _require_remote(config: Config) -> None:
    if not config.sync_remote:
        raise RuntimeError(
            "config 中未配置 sync.remote — 在 config.yaml 加入:\n"
            "  sync:\n    remote: <rclone-remote>:<bucket>"
        )


def push(config: Config, *, data: bool = True, state: bool = True,
         dry_run: bool = False) -> int:
    _check_rclone()
    _require_remote(config)
    targets: list[SyncTarget] = []
    if data:
        targets.extend(_data_targets(config))
    if state:
        targets.append(_state_target(config))
    if not targets:
        warn("nothing to push (--data-only/--state-only 互斥?)")
        return 0
    return _run_targets(targets, "push", dry_run)


def pull(config: Config, *, data: bool = True, state: bool = True,
         dry_run: bool = False) -> int:
    _check_rclone()
    _require_remote(config)
    targets: list[SyncTarget] = []
    if data:
        targets.extend(_data_targets(config))
    if state:
        targets.append(_state_target(config))
    if not targets:
        warn("nothing to pull")
        return 0
    return _run_targets(targets, "pull", dry_run)


def status(config: Config) -> int:
    _check_rclone()
    _require_remote(config)
    failed = 0
    for t in _data_targets(config) + [_state_target(config)]:
        banner(f"check {t.label}")
        rc = _rclone("check", str(t.local), t.remote, *t.flags, "--one-way")
        if rc != 0:
            warn(f"  ⚠ {t.label} 存在差异 (rclone exit={rc})")
            failed += 1
        else:
            info(f"  ✔ {t.label} 一致")
    return failed


def run_sync(args) -> None:
    config: Config = Config.load(args.config_path)
    action: str = args.action

    if action in ("push", "pull"):
        data = not args.state_only
        state = not args.data_only
        fn = push if action == "push" else pull
        failed = fn(config, data=data, state=state, dry_run=args.dry_run)
        banner(f"{action} 汇总")
        if failed:
            error(f"✘ 失败子任务: {failed}")
        else:
            info("✔ 全部成功")
        bottom()
    elif action == "status":
        failed = status(config)
        banner("status 汇总")
        if failed:
            warn(f"⚠ 不一致子任务: {failed}")
        else:
            info("✔ 完全一致")
        bottom()
    else:
        raise ValueError(f"unknown action: {action}")
