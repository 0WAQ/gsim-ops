"""ops resubmit — 将 ACTIVE 因子打回 staging 重新审查。

把 alpha_src/<name>/ 搬回 staging/<name>/、状态 ACTIVE → SUBMITTED,
下一次 ops check 会把它当作新提交重跑 8 阶段流水线。

destructive 为 opt-in:
- 默认仅搬 alpha_src + 翻状态;alpha_dump / alpha_feature / alpha_pnl 保留
- --purge:清除 alpha_dump + alpha_feature(alpha_pnl 始终保留,作为历史对照)

批量模式(-u / -s)采用 apt-install 风格交互:列出受影响因子后询问 y/N;
-y / --yes 跳过确认。

跨机:状态变更通过 ops sync push 的 state merge 传播;sync 不会删 remote
alpha_src(rclone copy 是 additive)。其他机器若需召回需自行 resubmit。
"""
import shutil
from datetime import datetime
from pathlib import Path

import xmltodict

from ops.infra.config import Config
from ops.infra.lock import factor_lock, FactorLocked
from ops.infra.store import default_store
from ops.core.state import FactorRecord, FactorStatus
from ops.services.rm.rm import _purge_artifacts
from ops.utils.logger.log import banner, bottom, info, warn, error, highlight


def _clean_pycache(root: Path) -> None:
    for p in root.rglob("__pycache__"):
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)


def _rewrite_module_path(d: Path) -> None:
    xmls = list(d.glob("*.xml"))
    pys = list(d.glob("*.py"))
    if not xmls or not pys:
        return
    xml_file = xmls[0]
    cfg = xmltodict.parse(xml_file.read_text(encoding="utf-8"))
    modules_alpha = cfg.get("gsim", {}).get("Modules", {}).get("Alpha")
    if isinstance(modules_alpha, dict):
        modules_alpha["@module"] = str(pys[0])
        xml_file.write_text(
            xmltodict.unparse(cfg, pretty=True, encoding="utf-8", full_document=False),
            encoding="utf-8",
        )


def _resolve_targets(args, store) -> list[FactorRecord]:
    name: str | None = args.factor_name
    if name:
        rec = store.get(name)
        if rec is None:
            error(f"  ✘ 因子 {name} 不在 state 中")
            return []
        if rec.status != FactorStatus.ACTIVE:
            error(f"  ✘ {name} 状态为 {rec.status.value},resubmit 仅支持 active")
            return []
        return [rec]

    # 批量
    status_enum = FactorStatus(args.status)
    if status_enum != FactorStatus.ACTIVE:
        error(f"  ✘ 目前 resubmit 仅支持 --status active")
        return []
    records = store.list(author=args.user, status=status_enum)
    records.sort(key=lambda r: r.name)
    return records


def _print_plan(targets: list[FactorRecord], purge: bool) -> None:
    highlight(f"  将 resubmit {len(targets)} 个因子(active → submitted):")
    for r in targets:
        info(f"    · {r.name:<40}  author={r.author}")
    if purge:
        highlight("  --purge: 同步清除 alpha_dump + alpha_feature(alpha_pnl 保留)")
    else:
        info("  (默认保留 alpha_dump / alpha_feature / alpha_pnl)")


def _resubmit_one(rec: FactorRecord, config: Config, store, purge: bool) -> None:
    name = rec.name
    src = config.alpha_src / name
    dst = config.staging / name

    if not src.exists():
        # 文件已不在 alpha_src,交给 reconcile 处理
        raise FileNotFoundError(f"{src} 不存在,跳过(状态可能已漂移,请先 ops check)")
    if dst.exists():
        raise FileExistsError(f"{dst} 已存在,拒绝覆盖")

    config.staging.mkdir(parents=True, exist_ok=True)
    _clean_pycache(src)

    # 先 move,再 transition:崩在中间由 reconcile 修(ACTIVE + in staging → SUBMITTED)
    shutil.move(str(src), str(dst))
    _rewrite_module_path(dst)

    if purge:
        removed = _purge_artifacts(name, config)
        for r in removed:
            info(f"    ✔ 已删除 {r}")

    store.transition(name, FactorStatus.SUBMITTED)
    info(f"  ✔ {name} active → submitted")


def run_resubmit(args) -> None:
    config: Config = Config.load(args.config_path)
    store = default_store()

    targets = _resolve_targets(args, store)
    if not targets:
        warn("  没有匹配的因子")
        return

    banner(f"resubmit · {len(targets)} 个因子")
    _print_plan(targets, purge=args.purge)

    if not args.yes:
        ans = input(f"  确认 resubmit? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            info("  已取消")
            bottom()
            return

    ok = fail = locked = 0
    for rec in targets:
        try:
            with factor_lock(rec.name):
                _resubmit_one(rec, config, store, purge=args.purge)
                ok += 1
        except FactorLocked:
            warn(f"  ⚠ {rec.name} 被另一个进程占用,跳过")
            locked += 1
        except Exception as e:
            error(f"  ✘ {rec.name} 失败: {e}")
            fail += 1

    info(f"  汇总: 成功={ok}  失败={fail}  占用={locked}")
    bottom()
