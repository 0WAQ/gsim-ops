"""One-shot migration: JsonStateStore -> RedisStateStore.

Usage:
    uv run python -m ops.tools.state_migrate \
        --json   ~/.cache/ops/lib/alphalib-juicefs/factor_state.json \
        --url    redis://127.0.0.1:6380/0 \
        --lib    alphalib-juicefs \
        --pass-env JFS_META_PASSWORD          # or --pass <literal>
        [--dry-run]
        [--reset]                              # wipe target state-index + per-factor keys first

Idempotent: re-running over the same JSON updates the redis records in place.
Use --reset only if you suspect the redis side has stale half-migrated data.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from ops.core.state import FactorRecord, CheckRecord
from ops.infra.store.redis_store import RedisStateStore


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", required=True, type=Path)
    ap.add_argument("--url",  required=True, help="redis://host:port/db (no password)")
    ap.add_argument("--lib",  required=True, help="library_id (matches config.yaml sync.library_id)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--pass",     dest="password", help="literal password (avoid; appears in ps)")
    g.add_argument("--pass-env", dest="password_env", help="env var holding the password")
    g.add_argument("--pass-file", dest="password_file", type=Path, help="file containing 'META_PASSWORD=...'")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reset",   action="store_true", help="wipe target keys before migrating")
    args = ap.parse_args()

    password = args.password
    if args.password_env:
        password = os.environ.get(args.password_env)
        if not password:
            print(f"env var {args.password_env} not set", file=sys.stderr)
            return 1
    if args.password_file:
        for line in args.password_file.read_text().splitlines():
            if line.startswith("META_PASSWORD="):
                password = line.split("=", 1)[1].strip()
                break

    if not args.json.exists():
        print(f"source json missing: {args.json}", file=sys.stderr)
        return 1

    raw = json.loads(args.json.read_text() or "{}")
    records = {k: FactorRecord.from_dict(v) for k, v in raw.items()}
    print(f"loaded {len(records)} records from {args.json}")

    if args.dry_run:
        print("dry-run; exiting")
        for n in list(records.keys())[:5]:
            r = records[n]
            print(f"  sample: {n}  status={r.status.value}  author={r.author}  checks={len(r.check_history)}")
        return 0

    store = RedisStateStore(url=args.url, library_id=args.lib, password=password)

    if args.reset:
        print(f"--reset: wiping target keys for library={args.lib}")
        # Read existing index + checks keys, then delete.
        names = store.r.smembers(store._index_key)
        if names:
            pipeline_keys = []
            for n in names:
                pipeline_keys.append(store._factor_key(n))
                pipeline_keys.append(store._checks_key(n))
            store.r.delete(*pipeline_keys)
        store.r.delete(store._index_key)
        print(f"  deleted {len(names)} factor records")

    written = 0
    for name, rec in records.items():
        # Use the store's put() so the index + hash both get written.
        # Then push the check_history separately (put() does NOT touch it).
        store.put(rec)
        if rec.check_history:
            ckey = store._checks_key(name)
            store.r.delete(ckey)  # clear in case of re-run
            with store.r.pipeline(transaction=False) as p:
                for c in rec.check_history:
                    p.rpush(ckey, json.dumps(c.to_dict(), ensure_ascii=False))
                p.execute()
        written += 1
        if written % 200 == 0:
            print(f"  {written}/{len(records)}")

    print(f"wrote {written} records")

    # Sanity check
    listed = store.list()
    print(f"verification: store.list() returns {len(listed)} records")
    if len(listed) != len(records):
        print(f"  WARN: count mismatch (json had {len(records)})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
