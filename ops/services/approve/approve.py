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
from ops.core.factor import Factor
from ops.core.state import CORRELATION, CheckRecord, FactorRecord, FactorStatus
from ops.infra.config import Config
from ops.infra.repository import FactorRepository
from ops.services._batch import BatchResult, SkipFactor, apply_locked, confirm_or_abort
from ops.utils.clock import now_iso as _now
from ops.utils.printer import banner, bottom, error, highlight, info, warn


def _eligible(rec: FactorRecord) -> bool:
    # 语义 API(2026-07-09):谓词在 FactorRecord 上,不再跨包问 check 的 stage 表
    return rec.correlation_rejected()


def _resolve_targets(args, repo: FactorRepository) -> tuple[list[Factor], list[tuple[Factor, str]]]:
    name: str | None = args.factor_name

    if name and args.user:
        error("  ✘ factor_name 与 -u 互斥")
        return [], []

    if name:
        factor = repo.get(name)
        if factor is None or factor.state is None:
            error(f"  ✘ 因子 {name} 不在 state 中")
            return [], []
        if factor.state.status != FactorStatus.REJECTED:
            error(f"  ✘ {name} 状态为 {factor.state.status.value},approve 仅支持 rejected")
            return [], []
        if factor.state.last_fail_stage != CORRELATION:
            error(f"  ✘ {name} 失败阶段为 {factor.state.last_fail_stage},approve 仅支持 correlation")
            return [], []
        return [factor], []

    if not args.user:
        error("  ✘ 必须指定 factor_name 或 -u")
        return [], []

    # 批量模式:单条三表 JOIN(author + REJECTED 一并下推;原先 info.list +
    # state.list 两次查 + 内存交集)
    factors = repo.find(author=args.user, status=FactorStatus.REJECTED)
    targets: list[Factor] = []
    skipped: list[tuple[Factor, str]] = []
    for f in factors:
        if f.state is not None and _eligible(f.state):
            targets.append(f)
        else:
            skipped.append((f, f"failed at {f.last_fail_stage or '?'}"))
    return targets, skipped


def _print_plan(targets: list[Factor],
                skipped: list[tuple[Factor, str]]) -> None:
    highlight(f"  将 approve {len(targets)} 个因子 → active:")
    for f in targets:
        rejected_at = f.state.rejected_at if f.state else None
        info(f"    · {f.name:<40}  author={f.identity.author or '?':<10}  rejected_at={rejected_at or '?'}")
    if skipped:
        highlight(f"  跳过 {len(skipped)} 个(非 correlation 失败):")
        for f, why in skipped:
            info(f"    · {f.name:<40}  {why}")


def _approve_one(rec: FactorRecord, repo: FactorRepository) -> None:
    name = rec.name

    now = _now()
    # CAS: 只允许 REJECTED → ACTIVE(FOR UPDATE 行锁内校验;expect 不符抛
    # StateConflict,由批量骨架按'跳过'处理)。原 transition 无 from-status
    # 守卫,任何状态都能被翻成 ACTIVE(full-review 第三部分 §3.2)。
    repo.transition(
        name,
        FactorStatus.ACTIVE,
        expect=FactorStatus.REJECTED,
        entered_at=rec.entered_at or now,
        last_fail_stage=None,
        last_fail_reason=None,
    )
    repo.append_check(name, CheckRecord(
        started_at=now,
        finished_at=now,
        passed=True,
        failed_stage=None,
        fail_reason="approved",
    ))


def run_approve(args) -> BatchResult | None:
    config: Config = Config.load(args.config_path)
    repo = FactorRepository(config)

    targets, skipped = _resolve_targets(args, repo)
    if not targets:
        if not skipped:
            warn("  没有匹配的因子")
        else:
            banner("approve · 0 个可处理")
            _print_plan(targets, skipped)
            bottom()
        return

    banner(f"approve · {len(targets)} 个因子")
    _print_plan(targets, skipped)

    if not confirm_or_abort("approve", len(targets), args.yes):
        bottom()
        return None

    def _action(name: str) -> None:
        # 锁内复验(TOCTOU):确认挂起期间因子可能已被 restage 召回 / rm 删除
        fresh = repo.record(name)
        if fresh is None:
            raise SkipFactor("state 记录已不存在")
        if not _eligible(fresh):
            raise SkipFactor(f"确认期间状态已变: status={fresh.status.value}, "
                             f"fail_stage={fresh.last_fail_stage}")
        _approve_one(fresh, repo)
        info(f"  ✔ {name} rejected → active")

    result = apply_locked([f.name for f in targets], config, _action, verb="approve")
    bottom()
    return result
