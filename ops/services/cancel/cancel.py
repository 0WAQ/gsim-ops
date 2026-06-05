"""ops cancel — 撤回未入库的因子(staging 里的 SUBMITTED)。

针对场景: QR 提交后发现因子不合规,在 ops check 之前撤掉。因子从未 ACTIVE
过,所以 state record 直接硬删,不留 DELETED tombstone(区别于 ops rm)。

默认仅 SUBMITTED;--force 也允许 CHECKING(从崩溃 / 中断的 check 残留中清出)。
真正在跑的 check 由 factor_lock 兜底拦截。

清理范围:
- staging/<name>/  (整个目录,硬删)
- state record     (store.delete,硬删)

不动: alpha_src / alpha_pnl / alpha_dump / alpha_feature
(SUBMITTED 因子按定义没有这些产物;CHECKING 残留若有 dump,留给 ops gc / 手工)

批量模式 (-u) apt 风格交互;-y 跳过确认。
"""
import shutil

from ops.infra.config import Config
from ops.infra.lock import factor_lock, FactorLocked
from ops.infra.store import default_store
from ops.core.state import FactorRecord, FactorStatus
from ops.utils.logger.log import banner, bottom, info, warn, error, highlight


def _eligible_statuses(force: bool) -> set[FactorStatus]:
    if force:
        return {FactorStatus.SUBMITTED, FactorStatus.CHECKING}
    return {FactorStatus.SUBMITTED}


def _resolve_targets(args, store) -> tuple[list[FactorRecord], list[tuple[FactorRecord, str]]]:
    name: str | None = args.factor_name
    eligible = _eligible_statuses(args.force)

    if name and args.user:
        error("  ✘ factor_name 与 -u 互斥")
        return [], []

    if name:
        rec = store.get(name)
        if rec is None:
            error(f"  ✘ 因子 {name} 不在 state 中")
            return [], []
        if rec.status not in eligible:
            hint = "submitted" if not args.force else "submitted/checking"
            error(f"  ✘ {name} 状态为 {rec.status.value},cancel 仅支持 {hint}"
                  f"{' (CHECKING 需 --force)' if rec.status == FactorStatus.CHECKING else ''}")
            return [], []
        return [rec], []

    if not args.user:
        error("  ✘ 必须指定 factor_name 或 -u")
        return [], []

    records = store.list(author=args.user)
    records.sort(key=lambda r: r.name)
    targets: list[FactorRecord] = []
    skipped: list[tuple[FactorRecord, str]] = []
    for r in records:
        if r.status in eligible:
            targets.append(r)
        else:
            skipped.append((r, f"status={r.status.value}"))
    return targets, skipped


def _print_plan(targets: list[FactorRecord],
                skipped: list[tuple[FactorRecord, str]],
                force: bool) -> None:
    highlight(f"  将 cancel {len(targets)} 个因子(删 staging + 删 state record):")
    for r in targets:
        info(f"    · {r.name:<40}  {r.status.value:<9}  author={r.author:<10}  "
             f"submitted_at={r.submitted_at or '?'}")
    if skipped:
        highlight(f"  跳过 {len(skipped)} 个(非 submitted{'/checking' if force else ''}):")
        for r, why in skipped:
            info(f"    · {r.name:<40}  {why}")
    if force:
        highlight("  --force: 同时允许 CHECKING(用于清理崩溃 / 中断的 check 残留)")


def _cancel_one(rec: FactorRecord, config: Config, store) -> None:
    name = rec.name
    staging_dir = config.staging / name

    # 先删 staging,再删 state — 崩在中间留下 orphan state record,
    # reconcile 会在下一次 ops check 启动时清掉 SUBMITTED/CHECKING 无文件因子
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
        info(f"    ✔ 已删除 staging/{name}/")
    else:
        warn(f"    ⚠ staging/{name}/ 不存在(可能已被外部清理)")

    if store.delete(name):
        info(f"    ✔ 已删除 state record {name}")
    else:
        warn(f"    ⚠ state record {name} 已不存在")


def run_cancel(args) -> None:
    config: Config = Config.load(args.config_path)
    store = default_store(config)

    targets, skipped = _resolve_targets(args, store)
    if not targets:
        if not skipped:
            warn("  没有匹配的因子")
        else:
            banner("cancel · 0 个可处理")
            _print_plan(targets, skipped, force=args.force)
            bottom()
        return

    banner(f"cancel · {len(targets)} 个因子")
    _print_plan(targets, skipped, force=args.force)

    if not args.yes:
        ans = input(f"  确认 cancel {len(targets)} 个因子? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            info("  已取消")
            bottom()
            return

    ok = fail = locked = 0
    for rec in targets:
        try:
            with factor_lock(rec.name):
                _cancel_one(rec, config, store)
                ok += 1
        except FactorLocked:
            warn(f"  ⚠ {rec.name} 被另一个进程占用(check 正在运行?),跳过")
            locked += 1
        except Exception as e:
            error(f"  ✘ {rec.name} 失败: {e}")
            fail += 1

    info(f"  汇总: 成功={ok}  失败={fail}  占用={locked}  跳过={len(skipped)}")
    bottom()
