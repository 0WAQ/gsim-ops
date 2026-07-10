"""ops clear — 清理 staging 里的孤儿目录(state record 缺失)。

由 ops submit 失败留下:`copy_to_staging` 已 copy,`submit_one` 里 parse 失败
return False,staging 目录留下但 state 没 put。命名不规范 / XML 异常 / py
syntax error 都会触发。

与 ops cancel 的分工:

| | ops cancel | ops clear |
|---|---|---|
| 适用 | state 有 record (SUBMITTED / CHECKING) | state 无 record (只有 staging 目录) |
| 清理 | staging + state record | 仅 staging 目录 |
| 反向触发 | state 无 record → 报错让用 clear | state 有 record → 报错让用 cancel |

孤儿全在 JFS staging 上,集中清理一次到位,不需跨机传播。
"""
import shutil
from pathlib import Path

from ops.core.paths import FactorPaths
from ops.infra.config import Config
from ops.infra.store import default_store
from ops.services._batch import BatchResult, SkipFactor, apply_locked, confirm_or_abort
from ops.services.submit.parser import _infer_author_from_dir
from ops.utils.printer import banner, bottom, error, highlight, info, warn


def _scan_staging_orphans(config: Config, store) -> list[Path]:
    """List staging/Alpha*/ dirs that have NO state record."""
    if not config.staging.exists():
        return []
    orphans: list[Path] = []
    for d in sorted(config.staging.iterdir()):
        if not d.is_dir() or not d.name.startswith("Alpha"):
            continue
        if store.get(d.name) is None:
            orphans.append(d)
    return orphans


def _resolve_targets(args, config: Config, store) -> tuple[list[Path], list[tuple[str, str]]]:
    """Return (orphan_dirs_to_clear, skipped[(name, reason)])."""
    name: str | None = args.factor_name

    if name and args.user:
        error("  ✘ factor_name 与 -u 互斥")
        return [], []

    if name:
        d = FactorPaths.of(name, config).staging
        if not d.exists():
            error(f"  ✘ staging/{name}/ 不存在")
            return [], []
        if not d.is_dir():
            error(f"  ✘ staging/{name} 不是目录")
            return [], []
        if store.get(name) is not None:
            error(f"  ✘ {name} 在 state 中有记录,请用 ops cancel(clear 仅处理孤儿)")
            return [], []
        return [d], []

    orphans = _scan_staging_orphans(config, store)
    if not args.user:
        return orphans, []

    # -u 过滤:用 _infer_author_from_dir,跟 submit/parser.py 一致
    matched: list[Path] = []
    skipped: list[tuple[str, str]] = []
    for d in orphans:
        author = _infer_author_from_dir(d.name)
        if author == args.user:
            matched.append(d)
        else:
            skipped.append((d.name, f"author={author}"))
    return matched, skipped


def _print_plan(targets: list[Path],
                skipped: list[tuple[str, str]]) -> None:
    highlight(f"  将 clear {len(targets)} 个 staging 孤儿(仅删目录,无 state record 可删):")
    for d in targets:
        author = _infer_author_from_dir(d.name)
        info(f"    · {d.name:<40}  author≈{author}")
    if skipped:
        highlight(f"  跳过 {len(skipped)} 个(不匹配 -u):")
        for name, why in skipped:
            info(f"    · {name:<40}  {why}")


def _clear_one(staging_dir: Path) -> None:
    shutil.rmtree(staging_dir)
    info(f"    ✔ 已删除 staging/{staging_dir.name}/")


def run_clear(args) -> BatchResult | None:
    config: Config = Config.load(args.config_path)
    store = default_store(config)

    targets, skipped = _resolve_targets(args, config, store)
    if not targets:
        if not skipped:
            warn("  没有匹配的 staging 孤儿")
        else:
            banner("clear · 0 个可处理")
            _print_plan(targets, skipped)
            bottom()
        return

    banner(f"clear · {len(targets)} 个 staging 孤儿")
    _print_plan(targets, skipped)

    if not confirm_or_abort("clear", len(targets), args.yes):
        bottom()
        return None

    dirs = {d.name: d for d in targets}

    def _action(name: str) -> None:
        # 锁内复验(TOCTOU):确认挂起期间可能有 submit 给该目录补上了 state
        # 记录 —— 那它就不再是孤儿,归 cancel 管
        if store.get(name) is not None:
            raise SkipFactor("确认期间出现 state 记录(不再是孤儿),改用 ops cancel")
        d = dirs[name]
        if not d.exists():
            raise SkipFactor("目录已被外部清理")
        _clear_one(d)

    result = apply_locked(list(dirs), config, _action, verb="clear")
    bottom()
    return result
