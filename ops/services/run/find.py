from pathlib import Path

from ops.core.factormeta import FactorMeta
from ops.infra.config import Config


def find_factor_dir(name: str, config: Config) -> Path | None:
    for root in [config.alpha_src, config.staging]:
        candidate = root / name
        if candidate.is_dir():
            return candidate
    return None


def scan_factors(
    users: list[str] | None,
    factor_name: str | None,
    config: Config,
) -> list[tuple[Path, str | None]]:
    """Scan alpha_src and staging for factors. Returns list of (dir, submitted_by)."""
    candidates: list[tuple[Path, str | None]] = []
    seen: set[str] = set()

    for root in [config.alpha_src, config.staging]:
        if not root.exists():
            continue

        if factor_name:
            d = root / factor_name
            if d.is_dir() and d.name not in seen:
                candidates.append((d, None))
                seen.add(d.name)
        else:
            for d in sorted(root.iterdir()):
                if d.is_dir() and d.name.startswith("Alpha") and d.name not in seen:
                    candidates.append((d, None))
                    seen.add(d.name)

    if users is None:
        return candidates

    # Filter by submitted_by from meta.json
    filtered: list[tuple[Path, str | None]] = []
    for factor_dir, _ in candidates:
        meta_path = factor_dir / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = FactorMeta.load(meta_path)
        except Exception:
            continue
        submitted_by = meta.submitted_by or meta.author or "unknown"
        if submitted_by in users:
            filtered.append((factor_dir, submitted_by))
    return filtered
