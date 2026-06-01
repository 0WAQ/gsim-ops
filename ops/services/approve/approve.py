"""ops approve — 人工审批 correlation 失败因子,REJECTED → ACTIVE。

仅适用于 `last_fail_stage == "correlation"` 的 REJECTED 因子。其他失败阶段
(checkbias/checkpoint/compliance) 是因子质量问题,不允许 approve。

数据产物在 correlation 失败时已就位(check.py to_recycle 保留 dump+pnl+feature),
approve 不重跑任何阶段,只删 recycle 归档目录 + 翻状态。
"""
import shutil

from ops.infra.config import Config
from ops.infra.lock import factor_lock, FactorLocked
from ops.infra.store import default_store
from ops.infra.store.json_store import _now
from ops.core.state import FactorRecord, FactorStatus, CheckRecord
from ops.utils.logger.log import banner, bottom, info, warn, error, highlight


_CORRELATION = "correlation"


def _eligible(rec: FactorRecord) -> bool:
    return rec.status == FactorStatus.REJECTED and rec.last_fail_stage == _CORRELATION


def _resolve_targets(args, store) -> tuple[list[FactorRecord], list[tuple[FactorRecord, str]]]:
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

    records = store.list(author=args.user, status=FactorStatus.REJECTED)
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
                skipped: list[tuple[FactorRecord, str]]) -> None:
    highlight(f"  将 approve {len(targets)} 个因子 → active:")
    for r in targets:
        info(f"    · {r.name:<40}  author={r.author:<10}  rejected_at={r.rejected_at or '?'}")
    if skipped:
        highlight(f"  跳过 {len(skipped)} 个(非 correlation 失败):")
        for r, why in skipped:
            info(f"    · {r.name:<40}  {why}")


def _approve_one(rec: FactorRecord, config: Config, store) -> None:
    name = rec.name
    recycle_dir = config.recycle / rec.author / _CORRELATION / name
    if recycle_dir.exists():
        shutil.rmtree(recycle_dir)

    now = _now()
    store.transition(
        name,
        FactorStatus.ACTIVE,
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


def run_approve(args) -> None:
    config: Config = Config.load(args.config_path)
    store = default_store()

    targets, skipped = _resolve_targets(args, store)
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

    if not args.yes:
        ans = input(f"  确认 approve {len(targets)} 个因子? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            info("  已取消")
            bottom()
            return

    ok = fail = locked = 0
    for rec in targets:
        try:
            with factor_lock(rec.name):
                _approve_one(rec, config, store)
                info(f"  ✔ {rec.name} rejected → active")
                ok += 1
        except FactorLocked:
            warn(f"  ⚠ {rec.name} 被另一个进程占用,跳过")
            locked += 1
        except Exception as e:
            error(f"  ✘ {rec.name} 失败: {e}")
            fail += 1

    info(f"  汇总: 成功={ok}  失败={fail}  占用={locked}  跳过={len(skipped)}")
    bottom()
