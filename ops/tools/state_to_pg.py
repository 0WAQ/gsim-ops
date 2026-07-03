"""One-shot migration: RedisStateStore -> PostgresStateStore.

Reads all state records from the current Redis backend and writes them into
Postgres, then reconciles (count + per-factor status/version/check_history).

    uv run python -m ops.tools.state_to_pg -c config.yaml [--dry-run]

The config must still have BOTH a working `state.redis.*` (source) and
`state.postgres.*` (target) block. This reads Redis via RedisStateStore and
writes PG via PostgresStateStore directly (not through default_store, which
would pick just one). Idempotent: put() is an upsert.

Redis data is only read, never modified — safe to re-run, safe to roll back
by flipping config.state.backend back to redis.
"""
import argparse
import sys
from pathlib import Path

from ops.infra.config import Config, get_default_config_path
from ops.infra.store.redis_store import RedisStateStore
from ops.infra.store.pg_store import PostgresStateStore
from ops.utils.printer import info, warn
from rich.console import Console

_stderr = Console(stderr=True)


def _reconcile(redis_recs, pg_store) -> bool:
    """Return True if PG matches Redis for every factor. Prints mismatches."""
    pg_recs = {r.name: r for r in pg_store.list()}
    ok = True
    if len(pg_recs) != len(redis_recs):
        warn(f"count mismatch: redis={len(redis_recs)} pg={len(pg_recs)}")
        ok = False
    for r in redis_recs:
        p = pg_recs.get(r.name)
        if p is None:
            warn(f"  missing in pg: {r.name}")
            ok = False
            continue
        if p.status != r.status:
            warn(f"  status differ {r.name}: redis={r.status.value} pg={p.status.value}")
            ok = False
        if p.version != r.version:
            warn(f"  version differ {r.name}: redis={r.version} pg={p.version}")
            ok = False
        if len(p.check_history) != len(r.check_history):
            warn(f"  check_history len differ {r.name}: "
                 f"redis={len(r.check_history)} pg={len(p.check_history)}")
            ok = False
        if p.last_fail_stage != r.last_fail_stage:
            warn(f"  last_fail_stage differ {r.name}")
            ok = False
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--config", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    config_path = args.config or get_default_config_path()
    config = Config.load(config_path)
    lib = config.library_id

    # Source: Redis (must be configured).
    url = getattr(config, "state_redis_url", None)
    if not url:
        _stderr.print("[red]config.state.redis.url missing — need redis as migration source[/]")
        return 1
    src = RedisStateStore(url=url, library_id=lib,
                          password=getattr(config, "state_redis_password", None))

    # Target: Postgres (must be configured).
    conninfo = getattr(config, "state_postgres_conninfo", None)
    if not conninfo:
        _stderr.print("[red]config.state.postgres.* missing — need postgres as migration target[/]")
        return 1
    dst = PostgresStateStore(conninfo=conninfo, library_id=lib)

    redis_recs = src.list()
    info(f"loaded {len(redis_recs)} records from redis (lib={lib})")

    if args.dry_run:
        info("dry-run; not writing")
        for r in redis_recs[:5]:
            info(f"  sample: {r.name} status={r.status.value} v{r.version} "
                 f"checks={len(r.check_history)}")
        return 0

    written = 0
    for r in redis_recs:
        dst.put(r, stamp=False)  # upsert full record, preserve original updated_at
        written += 1
        if written % 500 == 0:
            info(f"  {written}/{len(redis_recs)}")
    info(f"wrote {written} records to postgres")

    info("reconciling...")
    if _reconcile(redis_recs, dst):
        info(f"[reconcile OK] {len(redis_recs)} records match")
        return 0
    _stderr.print("[red]reconcile FAILED — see mismatches above[/]")
    return 1


if __name__ == "__main__":
    sys.exit(main())
