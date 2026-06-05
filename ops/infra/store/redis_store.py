"""Redis-backed StateStore. Single source of truth for multi-node ops.

Schema (1:1 mapping with JsonStateStore, scoped by library_id):

  state-meta:<lib>             hash    library-level: schema_version=v1
  state-index:<lib>            set     factor names (for list() without KEYS scan)
  state:<lib>:<name>           hash    FactorRecord scalar fields
  state-checks:<lib>:<name>    list    CheckRecord JSON entries

Concurrency:
- read-modify-write uses WATCH/MULTI/EXEC with retry on conflict.
- append_check is atomic (RPUSH + HSET updated_at in pipeline).
- list() reads index then HGETALL/LRANGE per record; no KEYS scan.

Failure mode: redis unreachable -> redis.exceptions.ConnectionError propagates.
Callers already expect StateStore methods to raise on backend failure (json_store
raises OSError on disk problems), so behavior is consistent.
"""
import json
from datetime import datetime
from urllib.parse import urlparse

import redis

from ops.core.state import FactorRecord, FactorStatus, CheckRecord
from .base import StateStore


SCHEMA_VERSION = "v1"
WATCH_MAX_RETRY = 16


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _record_to_hash(rec: FactorRecord) -> dict[str, str]:
    """FactorRecord -> hash payload (check_history stored separately)."""
    return {
        "name": rec.name,
        "author": rec.author,
        "status": rec.status.value,
        "updated_at": rec.updated_at,
        "submitted_at": rec.submitted_at or "",
        "submitted_by": rec.submitted_by or "",
        "entered_at": rec.entered_at or "",
        "rejected_at": rec.rejected_at or "",
        "deleted_at": rec.deleted_at or "",
        "last_fail_stage": rec.last_fail_stage or "",
        "last_fail_reason": rec.last_fail_reason or "",
        "version": str(rec.version),
    }


def _hash_to_record(h: dict[str, str], checks: list[CheckRecord]) -> FactorRecord:
    return FactorRecord(
        name=h["name"],
        author=h["author"],
        status=FactorStatus(h["status"]),
        updated_at=h["updated_at"],
        submitted_at=h.get("submitted_at") or None,
        submitted_by=h.get("submitted_by") or None,
        entered_at=h.get("entered_at") or None,
        rejected_at=h.get("rejected_at") or None,
        deleted_at=h.get("deleted_at") or None,
        last_fail_stage=h.get("last_fail_stage") or None,
        last_fail_reason=h.get("last_fail_reason") or None,
        version=int(h.get("version") or "1"),
        check_history=checks,
    )


class RedisStateStore(StateStore):
    def __init__(self, url: str, library_id: str, password: str | None = None):
        # Two URL shapes supported:
        #   redis://host:port/db                 -- direct, single instance
        #   redis-sentinel://h1:p1,h2:p2/svc/db  -- discover master via Sentinel
        #
        # Why parse ourselves instead of redis.from_url():
        # redis-py 8.x defaults to RESP3 / HELLO handshake; on a requirepass-only
        # server (no ACL user) HELLO fails with
        #   "HELLO must be called with the client already authenticated"
        # protocol=2 forces classic AUTH-then-commands. from_url() in 8.x has been
        # observed to ignore/override `protocol`. Direct construction is the
        # reliable knob.
        if url.startswith("redis-sentinel://"):
            self.r = self._sentinel_client(url, password)
        else:
            self.r = self._direct_client(url, password)
        self.lib = library_id
        self._meta_key = f"state-meta:{self.lib}"
        self._index_key = f"state-index:{self.lib}"
        # connection check + schema sentinel
        self.r.hsetnx(self._meta_key, "schema_version", SCHEMA_VERSION)

    @staticmethod
    def _direct_client(url: str, password: str | None) -> redis.Redis:
        parsed = urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 6379
        db_str = (parsed.path or "/0").lstrip("/")
        db = int(db_str) if db_str else 0
        pwd = password or parsed.password
        return redis.Redis(
            host=host,
            port=port,
            db=db,
            password=pwd,
            decode_responses=True,
            socket_timeout=5,
            protocol=2,
        )

    @staticmethod
    def _sentinel_client(url: str, password: str | None) -> redis.Redis:
        # redis-sentinel://h1:p1,h2:p2,h3:p3/master_name/db
        # netloc is "h1:p1,h2:p2,h3:p3"; path is "/master_name/db"
        from redis.sentinel import Sentinel

        parsed = urlparse(url)
        netloc = parsed.netloc
        # strip password@ if present (we pass password explicitly below)
        if "@" in netloc:
            cred, _, netloc = netloc.partition("@")
            if password is None and ":" in cred:
                password = cred.split(":", 1)[1]
            elif password is None:
                password = cred
        sentinels: list[tuple[str, int]] = []
        for hp in netloc.split(","):
            host, _, port_s = hp.strip().partition(":")
            if not host:
                continue
            sentinels.append((host, int(port_s) if port_s else 26379))
        if not sentinels:
            raise ValueError(f"redis-sentinel URL 没有 sentinel 节点: {url}")
        path_parts = [p for p in (parsed.path or "").split("/") if p]
        if not path_parts:
            raise ValueError(f"redis-sentinel URL 缺 master 名: {url}")
        service_name = path_parts[0]
        db = int(path_parts[1]) if len(path_parts) > 1 else 0

        s = Sentinel(
            sentinels,
            sentinel_kwargs={"socket_timeout": 5},
            socket_timeout=5,
        )
        # master_for returns a redis client that re-queries sentinels on each
        # connection -- transparent failover. Pass auth + protocol=2 explicitly.
        return s.master_for(
            service_name,
            db=db,
            password=password,
            decode_responses=True,
            socket_timeout=5,
            protocol=2,
        )

    def _factor_key(self, name: str) -> str:
        return f"state:{self.lib}:{name}"

    def _checks_key(self, name: str) -> str:
        return f"state-checks:{self.lib}:{name}"

    def _load_one(self, name: str) -> FactorRecord | None:
        h = self.r.hgetall(self._factor_key(name))
        if not h:
            return None
        checks_raw = self.r.lrange(self._checks_key(name), 0, -1)
        checks = [CheckRecord.from_dict(json.loads(c)) for c in checks_raw]
        return _hash_to_record(h, checks)

    # ---------------- StateStore interface ----------------

    def get(self, name: str) -> FactorRecord | None:
        return self._load_one(name)

    def put(self, record: FactorRecord) -> None:
        record.updated_at = _now()
        with self.r.pipeline(transaction=True) as p:
            p.hset(self._factor_key(record.name), mapping=_record_to_hash(record))
            p.sadd(self._index_key, record.name)
            # check_history is owned by append_check; put() does NOT rewrite it
            # to avoid races with concurrent appenders. Migration path handles
            # initial seeding.
            p.execute()

    def list(self,
             author: str | None = None,
             status: FactorStatus | None = None) -> list[FactorRecord]:
        names = self.r.smembers(self._index_key)
        if not names:
            return []
        # Bulk-fetch hashes with a pipeline; one round-trip for all.
        with self.r.pipeline(transaction=False) as p:
            for n in names:
                p.hgetall(self._factor_key(n))
            hashes = p.execute()
        out: list[FactorRecord] = []
        stale: list[str] = []
        check_names: list[str] = []
        for n, h in zip(names, hashes):
            if not h:
                stale.append(n)
                continue
            if author is not None and h.get("author") != author:
                continue
            if status is not None and h.get("status") != status.value:
                continue
            check_names.append(n)
        # Now fetch check_history only for the filtered subset.
        with self.r.pipeline(transaction=False) as p:
            for n in check_names:
                p.lrange(self._checks_key(n), 0, -1)
            check_lists = p.execute()
        name_to_hash = dict(zip(names, hashes))
        for n, checks_raw in zip(check_names, check_lists):
            checks = [CheckRecord.from_dict(json.loads(c)) for c in checks_raw]
            out.append(_hash_to_record(name_to_hash[n], checks))
        # GC stale index entries (record was deleted but index didn't follow).
        if stale:
            self.r.srem(self._index_key, *stale)
        return out

    def transition(self, name: str, to_status: FactorStatus, **updates) -> FactorRecord:
        fkey = self._factor_key(name)
        for attempt in range(WATCH_MAX_RETRY):
            with self.r.pipeline(transaction=True) as p:
                try:
                    p.watch(fkey)
                    h = p.hgetall(fkey)
                    if not h:
                        p.unwatch()
                        raise KeyError(f"factor not found: {name}")
                    rec = _hash_to_record(h, [])  # check_history irrelevant here
                    rec.status = to_status
                    for k, v in updates.items():
                        setattr(rec, k, v)
                    rec.updated_at = _now()
                    p.multi()
                    p.hset(fkey, mapping=_record_to_hash(rec))
                    p.sadd(self._index_key, name)
                    p.execute()
                    return rec
                except redis.WatchError:
                    continue
        raise RuntimeError(f"transition({name}) gave up after {WATCH_MAX_RETRY} retries")

    def append_check(self, name: str, check: CheckRecord) -> None:
        fkey = self._factor_key(name)
        ckey = self._checks_key(name)
        # Need to confirm the factor exists; do this in a WATCH/MULTI so a
        # concurrent delete() can't sneak in between EXISTS and RPUSH.
        for _ in range(WATCH_MAX_RETRY):
            with self.r.pipeline(transaction=True) as p:
                try:
                    p.watch(fkey)
                    if not p.exists(fkey):
                        p.unwatch()
                        raise KeyError(f"factor not found: {name}")
                    p.multi()
                    p.rpush(ckey, json.dumps(check.to_dict(), ensure_ascii=False))
                    p.hset(fkey, "updated_at", _now())
                    p.execute()
                    return
                except redis.WatchError:
                    continue
        raise RuntimeError(f"append_check({name}) gave up after {WATCH_MAX_RETRY} retries")

    def delete(self, name: str) -> bool:
        fkey = self._factor_key(name)
        ckey = self._checks_key(name)
        with self.r.pipeline(transaction=True) as p:
            p.exists(fkey)
            p.delete(fkey, ckey)
            p.srem(self._index_key, name)
            existed, _, _ = p.execute()
        return bool(existed)
