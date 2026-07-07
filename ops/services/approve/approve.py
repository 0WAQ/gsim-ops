"""ops approve — 因子库多样性 / 数据覆盖的人工豁免,REJECTED → ACTIVE。

自动流水线只优化业绩 + 低相关,盲区是**不看数据使用覆盖**:一个用了库里稀缺
数据的因子,若和某老因子相关、业绩又不占优,correlation stage 必拒且无自动路径可救
——哪怕它正是库最需要的(扩数据覆盖多样性)。approve 是对抗这个盲区的唯一人工闸:
人判定某因子对覆盖有独立价值,明知它相关/业绩不占优仍放行。详见 CLAUDE.md。

仅适用于 `last_fail_stage == "correlation"` 的 REJECTED 因子;其他阶段
(checkbias/checkpoint/compliance)是质量/正确性问题,不属豁免范畴。放行宽度是
整个 correlation stage(业绩门槛 + 相关性)——为覆盖保留因子本就可能接受它业绩差
一点,是有意的宽度。

数据产物在 correlation 失败时已就位(check.py on_reject 保留 dump+pnl+feature),
approve 不重跑任何阶段,只翻状态。
"""
from ops.core.state import CheckRecord, FactorRecord, FactorStatus
from ops.infra.config import Config
from ops.infra.info import default_info_store
from ops.infra.store import default_store
from ops.services._batch import BatchResult, SkipFactor, apply_locked, confirm_or_abort
from ops.services.check.stages import CORRELATION as _CORRELATION
from ops.utils.clock import now_iso as _now
from ops.utils.printer import banner, bottom, error, highlight, info, warn


def _eligible(rec: FactorRecord) -> bool:
    return rec.status == FactorStatus.REJECTED and rec.last_fail_stage == _CORRELATION


def _resolve_targets(args, store, info_store) -> tuple[list[FactorRecord], list[tuple[FactorRecord, str]]]:
    name: str | None = args.factor_name

    if name and args.user:
        error("  ✘ factor_name 与 -u 互斥")
        return [], []

    if name:
        rec = store.get(name)
        if rec is None:
            error(f"  ✘ 因子 {name} 不在 state 中")
            return [], []
        if rec.status != FactorStatus.REJECTED:
            error(f"  ✘ {name} 状态为 {rec.status.value},approve 仅支持 rejected")
            return [], []
        if rec.last_fail_stage != _CORRELATION:
            error(f"  ✘ {name} 失败阶段为 {rec.last_fail_stage},approve 仅支持 correlation")
            return [], []
        return [rec], []

    if not args.user:
        error("  ✘ 必须指定 factor_name 或 -u")
        return [], []

    # 批量模式：先从 info 获取符合 author 条件的 name 集合
    info_records = info_store.list(author=args.user)
    author_names = {i.name for i in info_records}

    # 再从 state 获取 REJECTED 记录
    records = store.list(status=FactorStatus.REJECTED)

    # 取交集
    records = [r for r in records if r.name in author_names]
    records.sort(key=lambda r: r.name)
    targets: list[FactorRecord] = []
    skipped: list[tuple[FactorRecord, str]] = []
    for r in records:
        if _eligible(r):
            targets.append(r)
        else:
            skipped.append((r, f"failed at {r.last_fail_stage or '?'}"))
    return targets, skipped


def _print_plan(targets: list[FactorRecord],
                skipped: list[tuple[FactorRecord, str]],
                info_store) -> None:
    # 批量获取 author 信息
    authors = {}
    for r in targets:
        info_rec = info_store.get(r.name)
        authors[r.name] = info_rec.author if info_rec else "?"

    highlight(f"  将 approve {len(targets)} 个因子 → active:")
    for r in targets:
        author = authors.get(r.name, "?")
        info(f"    · {r.name:<40}  author={author:<10}  rejected_at={r.rejected_at or '?'}")
    if skipped:
        highlight(f"  跳过 {len(skipped)} 个(非 correlation 失败):")
        for r, why in skipped:
            info(f"    · {r.name:<40}  {why}")


def _approve_one(rec: FactorRecord, store) -> None:
    name = rec.name

    now = _now()
    # CAS: 只允许 REJECTED → ACTIVE(FOR UPDATE 行锁内校验;expect 不符抛
    # StateConflict,由批量骨架按'跳过'处理)。原 transition 无 from-status
    # 守卫,任何状态都能被翻成 ACTIVE(full-review 第三部分 §3.2)。
    store.transition(
        name,
        FactorStatus.ACTIVE,
        expect=FactorStatus.REJECTED,
        entered_at=rec.entered_at or now,
        last_fail_stage=None,
        last_fail_reason=None,
    )
    store.append_check(name, CheckRecord(
        started_at=now,
        finished_at=now,
        passed=True,
        failed_stage=None,
        fail_reason="approved",
    ))


def run_approve(args) -> BatchResult | None:
    config: Config = Config.load(args.config_path)
    store = default_store(config)
    info_store = default_info_store(config)

    targets, skipped = _resolve_targets(args, store, info_store)
    if not targets:
        if not skipped:
            warn("  没有匹配的因子")
        else:
            banner("approve · 0 个可处理")
            _print_plan(targets, skipped, info_store)
            bottom()
        return

    banner(f"approve · {len(targets)} 个因子")
    _print_plan(targets, skipped, info_store)

    if not confirm_or_abort("approve", len(targets), args.yes):
        bottom()
        return None

    def _action(name: str) -> None:
        # 锁内复验(TOCTOU):确认挂起期间因子可能已被 restage 召回 / rm 删除
        fresh = store.get(name)
        if fresh is None:
            raise SkipFactor("state 记录已不存在")
        if not _eligible(fresh):
            raise SkipFactor(f"确认期间状态已变: status={fresh.status.value}, "
                             f"fail_stage={fresh.last_fail_stage}")
        _approve_one(fresh, store)
        info(f"  ✔ {name} rejected → active")

    result = apply_locked([r.name for r in targets], config, _action, verb="approve")
    bottom()
    return result
