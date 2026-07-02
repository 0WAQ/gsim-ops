"""Structured check report.

`ops check` 跑完落一份结构化 JSON 到 ~/.cache/ops/reports/,给 QR 发失败原因用。
完整 fail_reason(不截断)从 Redis check_history 读,不用 UI rows 里被截断的 note。

产物是可再生的运行记录(数据都在 Redis + metrics.json),放 cache 语义正好,
不进 git、不进 JFS 共享区、无需提权。一次 run 一份,不 rotation。
"""
import json
from datetime import datetime
from pathlib import Path

from ops.infra.config import Config
from ops.infra.cache import CACHE_ROOT
from ops.infra.store import default_store
from ops.services.list.metrics import load_metrics
from ops.utils.live_table import FactorRow


REPORT_VERSION = 1


def _report_dir() -> Path:
    d = CACHE_ROOT / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _scope(user: str | None, factor: str | None) -> str:
    """report 文件名里的范围段: -f 用因子名,-u 用用户名,否则 all。"""
    return factor or user or "all"


def write_check_report(config: Config, config_path: Path,
                       rows: dict[str, FactorRow],
                       *, user: str | None, factor: str | None) -> Path:
    """把本次 check 涉及因子的终态汇总成 JSON,返回文件路径。

    rows 是 parent 持有、LiveDriver 原地 mutate 过的 dict,已含 outcome_kind。
    每因子完整 check 记录从 store.get(name).check_history[-1] 读(本次刚 append)。
    pass 因子附 metrics。
    """
    store = default_store(config)
    metrics = load_metrics(config_path)

    factors: list[dict] = []
    summary = {"pass": 0, "fail": 0, "error": 0, "locked": 0}

    for name, row in rows.items():
        kind = row.outcome_kind or "error"
        summary[kind] = summary.get(kind, 0) + 1

        rec = store.get(name)
        last = rec.check_history[-1] if rec and rec.check_history else None
        m = metrics.get(name) if kind == "pass" else None

        factors.append({
            "name": name,
            "author": rec.author if rec else None,
            "status": rec.status.value if rec else None,
            "outcome": kind,
            "check": {
                "started_at": last.started_at if last else None,
                "finished_at": last.finished_at if last else None,
                "passed": last.passed if last else None,
                "failed_stage": last.failed_stage if last else None,
                "fail_reason": last.fail_reason if last else None,
            } if last else None,
            "metrics": m.to_dict() if m else None,
        })

    factors.sort(key=lambda f: f["name"])

    now = datetime.now()
    report = {
        "version": REPORT_VERSION,
        "generated_at": now.isoformat(timespec="seconds"),
        "library_id": config.library_id,
        "filter": {"user": user, "factor": factor},
        "summary": {"total": len(rows), **summary},
        "factors": factors,
    }

    ts = now.strftime("%Y%m%d-%H%M%S")
    path = _report_dir() / f"check-{_scope(user, factor)}-{ts}.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    return path
