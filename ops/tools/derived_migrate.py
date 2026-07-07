"""One-shot migration: legacy per-machine JSON caches -> DerivedStore.

Reads the four historical caches under ~/.cache/ops/lib/<lib>/:
  - index.json        (author / has_pnl / dump_days / delay)
  - metrics.json      (ret% / tvr% / shrp / mdd% / fitness)
  - datasources.json  (fields / tables)
  - bcorr.json        (max_bcorr / max_bcorr_factor)
and upserts them into whatever backend `config.derived_backend` selects
(json / postgres). Target defaults to the config's own derived store, so:

    uv run python -m ops.tools.derived_migrate -c config.yaml

migrates the current machine's caches into the configured (postgres) store.

Idempotent: re-running upserts in place. The four groups are written with
the store's group-wise upsert methods, so partial caches migrate cleanly
(a factor present only in metrics.json still lands, datasources left null).

This is a convenience/accelerator only. NOTE(2026-07-06): the derived layer has
been superseded by the three-table split (factor_info/state/snapshot); the old
`ops refresh` rebuild path was removed. This tool is retained for historical
reference only — new metrics are immutable snapshots written at archive time.
"""
import argparse
import json
import sys
from pathlib import Path

from rich.console import Console

from ops.infra.cache import CACHE_ROOT
from ops.infra.config import Config, get_default_config_path
from ops.infra.derived import default_derived_store
from ops.utils.printer import info, warn

_stderr = Console(stderr=True)


def _load(path: Path, key: str) -> dict:
    if not path.exists():
        warn(f"  skip (missing): {path.name}")
        return {}
    try:
        data = json.loads(path.read_text() or "{}")
    except Exception as e:
        warn(f"  skip (unreadable {e}): {path.name}")
        return {}
    return data.get(key, {})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--config", type=Path, default=None,
                    help="config path (default: OPS_CONFIG / ./config.yaml)")
    ap.add_argument("--cache-dir", type=Path, default=None,
                    help="override source cache dir (default ~/.cache/ops/lib/<library_id>/)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    config_path = args.config or get_default_config_path()
    config = Config.load(config_path)
    lib = config.library_id
    cache_dir = args.cache_dir or (CACHE_ROOT / "lib" / lib)
    info(f"library_id={lib}  backend={config.derived_backend}  cache_dir={cache_dir}")

    index = _load(cache_dir / "index.json", "factors")  # note: index is a list, handled below
    # index.json stores {"factors": [ {name, author, has_pnl, dump_days, delay}, ... ]}
    if isinstance(index, list):
        index_entries = {
            f["name"]: {
                "author": f.get("author"),
                "has_pnl": f.get("has_pnl"),
                "dump_days": f.get("dump_days"),
                "delay": f.get("delay"),
            }
            for f in index if f.get("name")
        }
    else:
        index_entries = {}

    metrics = _load(cache_dir / "metrics.json", "metrics")
    datasources = _load(cache_dir / "datasources.json", "datasources")
    bcorr = _load(cache_dir / "bcorr.json", "bcorr")

    info(f"loaded: index={len(index_entries)} metrics={len(metrics)} "
         f"datasources={len(datasources)} bcorr={len(bcorr)}")

    if args.dry_run:
        info("dry-run; not writing")
        return 0

    store = default_derived_store(config)

    if index_entries:
        store.upsert_index(index_entries)
        info(f"  upserted index for {len(index_entries)} factors")

    for name, m in metrics.items():
        store.upsert_metrics(name, {
            "ret": m.get("ret%"), "shrp": m.get("shrp"), "mdd": m.get("mdd%"),
            "tvr": m.get("tvr%"), "fitness": m.get("fitness"),
        })
    info(f"  upserted metrics for {len(metrics)} factors")

    for name, d in datasources.items():
        store.upsert_datasources(name, d.get("fields", []), d.get("tables", []))
    info(f"  upserted datasources for {len(datasources)} factors")

    for name, b in bcorr.items():
        if b.get("max_bcorr") is None:
            continue
        store.upsert_bcorr(name, b["max_bcorr"], b.get("max_bcorr_factor"))
    info(f"  upserted bcorr for {len(bcorr)} factors")

    total = len(store.get_all())
    info(f"done. store now has {total} factor rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
