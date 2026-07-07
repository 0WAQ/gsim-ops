"""ops cancel — 撤回未入库的因子(staging 里的 SUBMITTED)。

针对场景: QR 提交后发现因子不合规,在 ops check 之前撤掉。因子从未 ACTIVE
过,只需删 staging + 硬删 state record(无产物、无派生数据可清)。ops rm 则
删已入库因子的全部落点(src/pnl/dump/feature + factor_info 级联 state + snapshot)。

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

from ops.core.state import FactorRecord, FactorStatus
from ops.infra.config import Config
from ops.infra.info import default_info_store
from ops.infra.lock import FactorLocked, factor_lock
from ops.infra.store import default_store
from ops.utils.printer import banner, bottom, error, highlight, info, warn


def _eligible_statuses(force: bool) -> set[FactorStatus]:
    if force:
        return {FactorStatus.SUBMITTED, FactorStatus.CHECKING}
    return {FactorStatus.SUBMITTED}


def _resolve_targets(args, store, info_store) -> tuple[list[FactorRecord], list[tuple[FactorRecord, str]]]:
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
        if rec.entered_at:
            # 曾入库因子被 restage 召回后也是 SUBMITTED,但源码唯一副本在 staging
            # (restage 是 move 不是 copy)。cancel 的 rmtree 会毁掉唯一源码
            # (full-review 第一部分 1.2 / 第三部分 §3.1:"SUBMITTED(新)"与
            # "SUBMITTED(曾入库)"是被压成一个状态的两个状态,entered_at 即判据)。
            error(f"  ✘ {name} 曾入库(entered_at={rec.entered_at}),staging 里可能是"
                  f"唯一源码副本,拒绝 cancel;要彻底删除用 ops rm,要重新入库跑 ops check")
            return [], []
        return [rec], []

    if not args.user:
        error("  ✘ 必须指定 factor_name 或 -u")
        return [], []

    # 批量模式：先从 info 获取符合 author 条件的 name 集合
    info_records = info_store.list(author=args.user)
    author_names = {i.name for i in info_records}

    # 再从 state 获取所有记录
    records = store.list()

    # 取交集并按 eligible 筛选
    records = [r for r in records if r.name in author_names]
    records.sort(key=lambda r: r.name)
    targets: list[FactorRecord] = []
    skipped: list[tuple[FactorRecord, str]] = []
    for r in records:
        if r.status not in eligible:
            skipped.append((r, f"status={r.status.value}"))
        elif r.entered_at:
            # 曾入库(restage 召回态):staging 可能是唯一源码,批量里静默跳过并说明
            skipped.append((r, "曾入库,staging 或为唯一源码,用 rm/check"))
        else:
            targets.append(r)
    return targets, skipped


def _print_plan(targets: list[FactorRecord],
                skipped: list[tuple[FactorRecord, str]],
                info_store,
                force: bool) -> None:
    # 批量获取 author 信息
    authors = {}
    for r in targets:
        info_rec = info_store.get(r.name)
        authors[r.name] = info_rec.author if info_rec else "?"

    highlight(f"  将 cancel {len(targets)} 个因子(删 staging + 删 state record):")
    for r in targets:
        author = authors.get(r.name, "?")
        info(f"    · {r.name:<40}  {r.status.value:<9}  author={author:<10}  "
             f"submitted_at={r.submitted_at or '?'}")
    if skipped:
        highlight(f"  跳过 {len(skipped)} 个(非 submitted{'/checking' if force else ''}):")
        for r, why in skipped:
            info(f"    · {r.name:<40}  {why}")
    if force:
        highlight("  --force: 同时允许 CHECKING(用于清理崩溃 / 中断的 check 残留)")


def _cancel_one(rec: FactorRecord, config: Config, store, info_store) -> None:
    name = rec.name
    staging_dir = config.staging / name

    # 先删 staging,再删 state — 崩在中间留下 orphan state record(SUBMITTED、无文件)。
    # 不再自动清理(reconcile 已下线),但 ops check 按 staging 目录扫描,该 orphan 不影响
    # 后续流程;必要时人工 ops rm / 后续 doctor 处理。
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
        info(f"    ✔ 已删除 staging/{name}/")
    else:
        warn(f"    ⚠ staging/{name}/ 不存在(可能已被外部清理)")

    if store.delete(name):
        info(f"    ✔ 已删除 state record {name}")
    else:
        warn(f"    ⚠ state record {name} 已不存在")

    # FK 级联方向是 info→state,删 state 不会带走 info:不删则每次 cancel 泄漏一行
    # 孤儿 factor_info,且任何命令都够不到它(full-review P0-6 同族)。resolve 阶段的
    # entered_at 守卫保证走到这里的因子从未入库,身份行可以安全移除;重新 submit 会
    # 重建 info。
    if info_store.delete(name):
        info(f"    ✔ 已删除 factor_info {name}")


def run_cancel(args) -> None:
    config: Config = Config.load(args.config_path)
    store = default_store(config)
    info_store = default_info_store(config)

    targets, skipped = _resolve_targets(args, store, info_store)
    if not targets:
        if not skipped:
            warn("  没有匹配的因子")
        else:
            banner("cancel · 0 个可处理")
            _print_plan(targets, skipped, info_store, force=args.force)
            bottom()
        return

    banner(f"cancel · {len(targets)} 个因子")
    _print_plan(targets, skipped, info_store, force=args.force)

    if not args.yes:
        ans = input(f"  确认 cancel {len(targets)} 个因子? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            info("  已取消")
            bottom()
            return

    ok = fail = locked = 0
    for rec in targets:
        try:
            with factor_lock(rec.name, config):
                _cancel_one(rec, config, store, info_store)
                ok += 1
        except FactorLocked:
            warn(f"  ⚠ {rec.name} 被另一个进程占用(check 正在运行?),跳过")
            locked += 1
        except Exception as e:
            error(f"  ✘ {rec.name} 失败: {e}")
            fail += 1

    info(f"  汇总: 成功={ok}  失败={fail}  占用={locked}  跳过={len(skipped)}")
    bottom()
