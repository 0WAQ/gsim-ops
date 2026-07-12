"""ops cancel — 撤回未入库的因子(staging 里的 SUBMITTED)。

针对场景: QR 提交后发现因子不合规,在 ops check 之前撤掉。因子从未 ACTIVE
过,只需删 staging + 硬删 state record(无产物、无派生数据可清)。ops rm 则
删已入库因子的全部落点(src/pnl/dump/feature + factor_info 级联 state + snapshot)。

默认仅 SUBMITTED;--force 也允许 CHECKING(从崩溃 / 中断的 check 残留中清出)。
真正在跑的 check 由 factor_lock 兜底拦截。

清理范围:
- staging/<name>/  (整个目录,硬删)
- state record     (store.delete,硬删)

不动: alpha_src / alpha_pnl / alpha_dump / alpha_feature —— 正因如此,资格判定
拒绝任何在 alpha_src 有归档的因子(entered_at 非空的曾 ACTIVE、或曾 REJECTED 后
重提的):对它们只删记录会留下孤儿产物,须走 ops rm(2026-07-09 生产实测,JOURNAL U3)。

批量模式 (-u) apt 风格交互;-y 跳过确认。
"""
from ops.core.factor import Factor, FactorIdentity
from ops.core.state import FactorRecord, FactorStatus
from ops.infra.config import Config
from ops.infra.repository import FactorRepository
from ops.services._batch import BatchResult, SkipFactor, apply_locked, confirm_or_abort
from ops.utils.printer import banner, bottom, error, highlight, info, warn


def _eligible_statuses(force: bool) -> set[FactorStatus]:
    if force:
        return {FactorStatus.SUBMITTED, FactorStatus.CHECKING}
    return {FactorStatus.SUBMITTED}


def _ineligible_reason(rec: FactorRecord, force: bool, repo: FactorRepository) -> str | None:
    """resolve 与锁内复验共用的资格谓词;返回 None=可 cancel,str=原因。"""
    if rec.status not in _eligible_statuses(force):
        return f"status={rec.status.value}"
    if rec.entered_at:
        return "曾入库(entered_at 非空),staging 或为唯一源码"
    if repo.paths(rec.name).src.exists():
        # cancel 的前提"SUBMITTED 无产物"只对纯新提交成立。曾被 check 归档过的
        # 因子(如 REJECTED 后 submit --overwrite 召回)在 alpha_src 有归档,
        # late-stage 拒绝还留有 pnl/dump —— 只删记录会把这些产物变成任何命令都
        # 够不到的孤儿(2026-07-09 生产实测:143 个孤儿目录即此路径产生,
        # JOURNAL U3)。拒绝并指引 ops rm(全落点删除)。
        return "alpha_src 有归档产物(曾被 check 归档),cancel 会留孤儿;用 ops rm"
    return None


def _resolve_targets(args, repo: FactorRepository) -> tuple[list[Factor], list[tuple[Factor, str]]]:
    name: str | None = args.factor_name
    eligible = _eligible_statuses(args.force)

    if name and args.user:
        error("  ✘ factor_name 与 -u 互斥")
        return [], []

    if name:
        rec = repo.record(name)
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
        if repo.paths(name).src.exists():
            error(f"  ✘ {name} 在 alpha_src 有归档产物(曾被 check 归档,如 REJECTED"
                  f" 后重提),cancel 只删记录会留下孤儿产物;要彻底删除用 ops rm")
            return [], []
        # repo.get 组全景(author 供 plan 显示);info 行异常缺失时(理论上
        # FK 保证不会)退化为仅 state 的聚合,不因显示信息缺失拒绝 cancel。
        f = repo.get(name) or Factor(identity=FactorIdentity(name=name), state=rec)
        return [f], []

    if not args.user:
        error("  ✘ 必须指定 factor_name 或 -u")
        return [], []

    # 批量模式:单条三表 JOIN,全状态(资格谓词把非 eligible 的归 Skipped 段;
    # 原先 info.list + state.list() 两次查 + 内存交集)
    factors = repo.find(author=args.user, include_submitted=True)
    targets: list[Factor] = []
    skipped: list[tuple[Factor, str]] = []
    for f in factors:
        if f.state is None:
            skipped.append((f, "无 state 记录(info 孤儿,需对账)"))
            continue
        reason = _ineligible_reason(f.state, args.force, repo)
        if reason:
            skipped.append((f, reason))
        else:
            targets.append(f)
    return targets, skipped


def _print_plan(targets: list[Factor],
                skipped: list[tuple[Factor, str]],
                force: bool) -> None:
    highlight(f"  将 cancel {len(targets)} 个因子(删 staging + 删 state record):")
    for f in targets:
        status = f.status.value if f.status else "?"
        submitted_at = f.state.submitted_at if f.state else None
        info(f"    · {f.name:<40}  {status:<9}  author={f.identity.author or '?':<10}  "
             f"submitted_at={submitted_at or '?'}")
    if skipped:
        highlight(f"  跳过 {len(skipped)} 个(非 submitted{'/checking' if force else ''}):")
        for f, why in skipped:
            info(f"    · {f.name:<40}  {why}")
    if force:
        highlight("  --force: 同时允许 CHECKING(用于清理崩溃 / 中断的 check 残留)")


def _cancel_one(name: str, repo: FactorRepository) -> None:
    # 先删 staging,再删记录 — 崩在中间留下 orphan record(SUBMITTED、无文件)。
    # 不再自动清理(reconcile 已下线),但 ops check 按 staging 目录扫描,该 orphan 不影响
    # 后续流程;必要时人工 ops rm / 后续 doctor 处理。
    if repo.unstage(name):
        info(f"    ✔ 已删除 staging/{name}/")
    else:
        warn(f"    ⚠ staging/{name}/ 不存在(可能已被外部清理)")

    # repo.delete = 删 factor_info,FK 级联带走 state(原先分两步"先 state 再
    # info",顺序反了会泄漏孤儿 info 行,full-review P0-6 同族;级联一步消灭
    # 中间态)。resolve 阶段的 entered_at 守卫保证走到这里的因子从未入库,
    # 身份行可安全移除;重新 submit 会重建。
    if repo.delete(name, op="cancel"):
        info(f"    ✔ 已删除 factor_info + 级联 state record {name}")
    else:
        warn(f"    ⚠ 记录 {name} 已不存在")


def run_cancel(args) -> BatchResult | None:
    config: Config = Config.load(args.config_path)
    repo = FactorRepository(config)

    targets, skipped = _resolve_targets(args, repo)
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

    if not confirm_or_abort("cancel", len(targets), args.yes):
        bottom()
        return None

    def _action(name: str) -> None:
        # 锁内复验:确认提示挂起期间因子可能已被 check 转走 / 重新入库
        fresh = repo.record(name)
        if fresh is None:
            raise SkipFactor("state 记录已不存在")
        reason = _ineligible_reason(fresh, args.force, repo)
        if reason:
            raise SkipFactor(f"确认期间状态已变: {reason}")
        _cancel_one(name, repo)

    result = apply_locked([f.name for f in targets], config, _action, verb="cancel")
    bottom()
    return result
