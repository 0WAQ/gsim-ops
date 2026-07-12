"""ops restage — 把已入库因子召回 staging,等待重跑 check(原代码不变)。

restage 本身不跑回测:它只把因子从 alpha_src 搬回 staging/<name>/、状态翻为
SUBMITTED,让下一次 ops check 捡起重跑 7 阶段流水线。version 不变。

支持的来源状态:
- ACTIVE   (默认): 源 = alpha_src/<name>/
- REJECTED        : 源 = alpha_src/<name>/(REJECTED src 与 ACTIVE 同库)

产物按两个面处理(2026-07-08 PV7):
- **check 面**(alpha_pnl + bcorr 池副本 + snapshot):离库即失效,**一律回收**
  —— 旧 pnl 留在池里是"自鬼影"(重检时对自己旧 pnl corr≈1,高相关分支要求
  打败几乎相同的自己 → 必拒),与"离库删 snapshot"(R1)同构;
- **服务面**(alpha_dump / alpha_feature):语义 = 最后一次入库版本的
  last-known-good,生产 combo 在重检窗口内继续消费,**默认保留**;
  --purge = 立即下架(同步清除);REJECTED 召回无服务价值,一律自动清。

批量模式(-u / -s)采用 apt-install 风格交互:列出受影响因子后询问 y/N;
-y / --yes 跳过确认。

跨机:state 在共享 PG、staging 在共享 JFS,任一节点 restage 全局立即生效。
"""
from pathlib import Path

from ops.core.factor import Factor
from ops.core.state import FactorRecord, FactorStatus
from ops.infra.config import Config
from ops.infra.repository import ArtifactScope, FactorRepository
from ops.services._batch import BatchResult, SkipFactor, apply_locked, confirm_or_abort
from ops.utils.printer import banner, bottom, error, highlight, info, warn

_SUPPORTED_STATUSES = {FactorStatus.ACTIVE, FactorStatus.REJECTED}


def _locate_source(rec: FactorRecord, repo: FactorRepository) -> Path | None:
    """按状态定位因子源目录。返回 None 表示无法找到可搬运的源。"""
    if rec.status in (FactorStatus.ACTIVE, FactorStatus.REJECTED):
        src = repo.paths(rec.name).src
        return src if src.exists() else None
    return None


def _resolve_targets(args, repo: FactorRepository) -> list[Factor]:
    name: str | None = args.factor_name

    # 与 approve/cancel/clear 对齐:name 与 -u 互斥(原先静默忽略 -u,是
    # clone-and-edit 漂移;full-review 第二部分 §3.4)。
    if name and args.user:
        error("  ✘ factor_name 与 -u 互斥")
        return []

    if name:
        factor = repo.get(name)
        if factor is None or factor.state is None:
            error(f"  ✘ 因子 {name} 不在 state 中")
            return []
        if factor.state.status not in _SUPPORTED_STATUSES:
            error(f"  ✘ {name} 状态为 {factor.state.status.value},restage 不支持")
            return []
        return [factor]

    # 批量模式守卫:必须显式给 -u 和/或 -s。--status 的 argparse 默认值为 None,
    # 否则本守卫永远不触发、裸 `ops restage` 会解析出全库 ACTIVE 因子。
    if not args.user and not args.status:
        error("  ✘ 批量模式必须指定 -u 和/或 -s(裸 restage 意味着召回全库,拒绝)")
        return []

    status_enum = FactorStatus(args.status) if args.status else FactorStatus.ACTIVE
    if status_enum not in _SUPPORTED_STATUSES:
        error(f"  ✘ --status 仅支持: {', '.join(s.value for s in _SUPPORTED_STATUSES)}")
        return []

    # 单条三表 JOIN(author + status 一并下推;原先 info.list + state.list
    # 两次查 + 内存交集)
    factors = repo.find(author=args.user, status=status_enum)
    return factors  # find 已按 name 排序


def _print_plan(targets: list[Factor],
                sources: dict[str, Path | None],
                purge: bool) -> None:
    highlight(f"  将 restage {len(targets)} 个因子 → submitted:")
    for f in targets:
        src = sources.get(f.name)
        src_str = str(src) if src else "✘ 源缺失"
        status = f.status.value if f.status else "?"
        info(f"    · {f.name:<40}  {status:<9}  author={f.identity.author or '?':<10}  ← {src_str}")
    if purge:
        highlight("  --purge: 立即下架 —— 同步清除 alpha_dump + alpha_feature")
    else:
        info("  (dump/feature 保留为服务面 last-known-good;pnl + bcorr 池副本一律回收)")
    # 确认提示必须与 _restage_one 的实际行为一致:REJECTED 不看 --purge 一律清产物
    if any(r.status == FactorStatus.REJECTED for r in targets):
        highlight("  REJECTED 因子将自动清除 dump + feature")


def _restage_one(rec: FactorRecord, src: Path, config: Config,
                 repo: FactorRepository, purge: bool) -> None:
    name = rec.name

    py_files = sorted(src.glob("*.py"))
    xml_files = sorted(src.glob("*.xml"))
    if len(py_files) != 1 or len(xml_files) != 1:
        raise ValueError(
            f"文件数不合规: .py={[p.name for p in py_files]}, "
            f".xml={[x.name for x in xml_files]} (各需恰好 1 个)"
        )

    # 先 move,再 transition:崩在中间留 orphan(reconcile 已下线),必要时人工
    # 处理。搬运 + @module 重指收编 repo.recall(2026-07-10;存在性/占用校验在
    # 其内,move 不是 copy —— 召回后 staging 是唯一副本)。
    prev_status = rec.status.value
    repo.recall(name)

    # 服务面(dump/feature):REJECTED 无服务价值自动清;ACTIVE 默认保留
    # (last-known-good 供生产 combo 继续消费),--purge = 立即下架
    if rec.status == FactorStatus.REJECTED or purge:
        for r in repo.purge_artifacts(name, ArtifactScope.SERVING):
            info(f"    ✔ 已删除 {r}")

    # check 面(pnl + bcorr 池副本):离库即失效,一律回收 —— 否则重检时
    # correlation 拿新 pnl 对池里自己的旧 pnl(corr≈1)必拒(自鬼影,PV7)
    for r in repo.purge_artifacts(name, ArtifactScope.CHECK):
        info(f"    ✔ 已回收 {r}")

    # CAS: 只允许从召回前状态(ACTIVE/REJECTED)翻 SUBMITTED
    repo.transition(name, FactorStatus.SUBMITTED, expect=rec.status, op="restage")

    # 离库 → 旧快照失效。快照语义是"入库事件的不可变快照",re-check 通过后 archive
    # 会写新快照;不删则 insert 撞 name UNIQUE 被吞,反查/报告永远停在旧代码的指标
    # (full-review P0-1)。删失败不阻断(archive 侧有 stale 自愈兜底)。
    try:
        repo.discard_snapshot(name)
    except Exception as e:
        warn(f"    ⚠ 删除旧 snapshot 失败(archive 时会自愈): {e}")

    info(f"  ✔ {name} {prev_status} → submitted")


def run_restage(args) -> BatchResult | None:
    config: Config = Config.load(args.config_path)
    repo = FactorRepository(config)

    targets = _resolve_targets(args, repo)
    if not targets:
        warn("  没有匹配的因子")
        return

    sources: dict[str, Path | None] = {
        f.name: _locate_source(f.state, repo) for f in targets if f.state is not None}

    banner(f"restage · {len(targets)} 个因子")
    _print_plan(targets, sources, purge=args.purge)

    missing = [f.name for f in targets if sources.get(f.name) is None]
    if missing:
        warn(f"  ⚠ {len(missing)} 个因子源缺失,将被跳过(可能需要 ops submit 重新提交)")

    runnable = [(f, src) for f in targets if (src := sources.get(f.name)) is not None]
    if not runnable:
        error("  ✘ 没有可处理的因子")
        bottom()
        return

    if not confirm_or_abort("restage", len(runnable), args.yes):
        bottom()
        return None

    src_by_name = {f.name: s for f, s in runnable}

    def _action(name: str) -> None:
        # 锁内复验(TOCTOU):确认挂起期间因子可能已被 check/rm/overwrite 动过
        fresh = repo.record(name)
        if fresh is None:
            raise SkipFactor("state 记录已不存在")
        if fresh.status not in _SUPPORTED_STATUSES:
            raise SkipFactor(f"确认期间状态已变: status={fresh.status.value}")
        src = src_by_name[name]
        if not src.exists():
            raise SkipFactor("源目录已不存在")
        _restage_one(fresh, src, config, repo, purge=args.purge)

    result = apply_locked([f.name for f, _ in runnable], config, _action, verb="restage")
    if missing:
        info(f"  (另有 {len(missing)} 个源缺失,resolve 阶段已跳过)")
    bottom()
    return result
