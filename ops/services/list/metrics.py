from pathlib import Path

from ops.core.metrics import Metrics
from ops.infra.config import Config
from ops.infra.derived import default_derived_store
from ops.infra.gsim.runner import Runner


def _store(config_path: Path):
    return default_derived_store(Config.load(config_path))


def _to_metrics(rec) -> Metrics | None:
    """DerivedRecord -> Metrics, None if metrics group never computed."""
    if rec.ret is None and rec.shrp is None and rec.fitness is None:
        return None
    return Metrics(
        ret=rec.ret, tvr=rec.tvr, shrp=rec.shrp, mdd=rec.mdd, fitness=rec.fitness,
    )


def _metrics_payload(m: Metrics) -> dict:
    return {"ret": m.ret, "shrp": m.shrp, "mdd": m.mdd, "tvr": m.tvr, "fitness": m.fitness}


def load_metrics(config_path: Path) -> dict[str, Metrics]:
    out: dict[str, Metrics] = {}
    for name, rec in _store(config_path).get_all().items():
        m = _to_metrics(rec)
        if m is not None:
            out[name] = m
    return out


def refresh_metrics(
    names: list[str], config: Config, config_path: Path
) -> dict[str, Metrics]:
    """Recompute metrics for the given factor names (runs simsummary), write to
    the store. Paths are rebuilt from config. Skips factors with no pnl."""
    store = _store(config_path)
    for name in names:
        pnl_path = config.alpha_pnl / name
        if not pnl_path.exists():
            continue
        result = Runner.run_simsummary(pnl_path, config)
        if result:
            store.upsert_metrics(name, _metrics_payload(result))
    return load_metrics(config_path)


def update_metrics(config_path: Path, name: str, m: Metrics) -> None:
    _store(config_path).upsert_metrics(name, _metrics_payload(m))
