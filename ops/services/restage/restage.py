"""ops restage — 把已入库因子召回 staging,等待重跑 check(原代码不变)。

restage 本身不跑回测:它只把因子从 alpha_src 搬回 staging/<name>/、状态翻为
SUBMITTED,让下一次 ops check 捡起重跑 8 阶段流水线。version 不变。

支持的来源状态:
- ACTIVE   (默认): 源 = alpha_src/<name>/
- REJECTED        : 源 = alpha_src/<name>/(REJECTED src 与 ACTIVE 同库)

destructive 为 opt-in:
- 默认仅搬源 + 翻状态;alpha_dump / alpha_feature / alpha_pnl 保留
- --purge:清除 alpha_dump + alpha_feature(alpha_pnl 始终保留,作为历史对照)

批量模式(-u / -s)采用 apt-install 风格交互:列出受影响因子后询问 y/N;
-y / --yes 跳过确认。

跨机:状态变更通过 ops sync push 的 state merge 传播;sync 不会删 remote
源目录(rclone copy 是 additive)。其他机器若需召回需自行 restage。
"""
import shutil
from pathlib import Path

import xmltodict

from ops.infra.config import Config
from ops.infra.lock import factor_lock, FactorLocked
from ops.infra.store import default_store
from ops.core.state import FactorRecord, FactorStatus
from ops.services.rm.rm import _purge_artifacts
from ops.utils.printer import banner, bottom, info, warn, error, highlight


_SUPPORTED_STATUSES = {FactorStatus.ACTIVE, FactorStatus.REJECTED}


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


def _locate_source(rec: FactorRecord, config: Config) -> Path | None:
    """按状态定位因子源目录。返回 None 表示无法找到可搬运的源。"""
    name = rec.name
    if rec.status == FactorStatus.ACTIVE:
        src = config.alpha_src / name
        return src if src.exists() else None
    if rec.status == FactorStatus.REJECTED:
        src = config.alpha_src / name
        return src if src.exists() else None
    return None


def _resolve_targets(args, store, config: Config) -> list[FactorRecord]:
    name: str | None = args.factor_name
    status_enum = FactorStatus(args.status)

    if status_enum not in _SUPPORTED_STATUSES:
        error(f"  ✘ --status 仅支持: {', '.join(s.value for s in _SUPPORTED_STATUSES)}")
        return []

    if name:
        rec = store.get(name)
        if rec is None:
            error(f"  ✘ 因子 {name} 不在 state 中")
            return []
        if rec.status not in _SUPPORTED_STATUSES:
            error(f"  ✘ {name} 状态为 {rec.status.value},restage 不支持")
            return []
        return [rec]

    if not args.user and not args.status:
        error("  ✘ 必须指定 factor_name 或 -u / -s")
        return []

    records = store.list(author=args.user, status=status_enum)
    records.sort(key=lambda r: r.name)
    return records


def _print_plan(targets: list[FactorRecord],
                sources: dict[str, Path | None],
                purge: bool) -> None:
    highlight(f"  将 restage {len(targets)} 个因子 → submitted:")
    for r in targets:
        src = sources.get(r.name)
        src_str = str(src) if src else "✘ 源缺失"
        info(f"    · {r.name:<40}  {r.status.value:<9}  author={r.author:<10}  ← {src_str}")
    if purge:
        highlight("  --purge: 同步清除 alpha_dump + alpha_feature(alpha_pnl 保留)")
    else:
        info("  (默认保留 alpha_dump / alpha_feature / alpha_pnl)")


def _restage_one(rec: FactorRecord, src: Path, config: Config, store, purge: bool) -> None:
    name = rec.name
    dst = config.staging / name

    if not src.exists():
        raise FileNotFoundError(f"{src} 不存在")
    if dst.exists():
        raise FileExistsError(f"{dst} 已存在,拒绝覆盖")

    py_files = sorted(src.glob("*.py"))
    xml_files = sorted(src.glob("*.xml"))
    if len(py_files) != 1 or len(xml_files) != 1:
        raise ValueError(
            f"文件数不合规: .py={[p.name for p in py_files]}, "
            f".xml={[x.name for x in xml_files]} (各需恰好 1 个)"
        )

    config.staging.mkdir(parents=True, exist_ok=True)
    _clean_pycache(src)

    # 先 move,再 transition:崩在中间留 orphan(reconcile 已下线),必要时人工处理
    prev_status = rec.status.value
    shutil.move(str(src), str(dst))
    _rewrite_module_path(dst)

    # REJECTED restage 自动清掉产物(无生产顾虑,check 会重新产出)
    # ACTIVE 仅在 --purge 时清
    if rec.status == FactorStatus.REJECTED or purge:
        removed = _purge_artifacts(name, config)
        for r in removed:
            info(f"    ✔ 已删除 {r}")
        # REJECTED 额外清 pnl
        # alpha_pnl/<name> 是单文件,不是目录
        if rec.status == FactorStatus.REJECTED:
            pnl = config.alpha_pnl / name
            if pnl.exists():
                pnl.unlink()
                info(f"    ✔ 已删除 alpha_pnl/{name}")

    store.transition(name, FactorStatus.SUBMITTED)
    info(f"  ✔ {name} {prev_status} → submitted")


def run_restage(args) -> None:
    config: Config = Config.load(args.config_path)
    store = default_store(config)

    targets = _resolve_targets(args, store, config)
    if not targets:
        warn("  没有匹配的因子")
        return

    sources: dict[str, Path | None] = {r.name: _locate_source(r, config) for r in targets}

    banner(f"restage · {len(targets)} 个因子")
    _print_plan(targets, sources, purge=args.purge)

    missing = [r.name for r in targets if sources[r.name] is None]
    if missing:
        warn(f"  ⚠ {len(missing)} 个因子源缺失,将被跳过(可能需要 ops submit 重新提交)")

    runnable = [r for r in targets if sources[r.name] is not None]
    if not runnable:
        error("  ✘ 没有可处理的因子")
        bottom()
        return

    if not args.yes:
        ans = input(f"  确认 restage {len(runnable)} 个因子? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            info("  已取消")
            bottom()
            return

    ok = fail = locked = 0
    for rec in runnable:
        try:
            with factor_lock(rec.name):
                _restage_one(rec, sources[rec.name], config, store, purge=args.purge)
                ok += 1
        except FactorLocked:
            warn(f"  ⚠ {rec.name} 被另一个进程占用,跳过")
            locked += 1
        except Exception as e:
            error(f"  ✘ {rec.name} 失败: {e}")
            fail += 1

    skipped = len(missing)
    info(f"  汇总: 成功={ok}  失败={fail}  占用={locked}  源缺失={skipped}")
    bottom()
