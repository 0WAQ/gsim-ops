"""Postgres 派生层后端.

单张 factor_derived 宽表 (schema 见 scripts/postgres/init/01-schema.sql),
以 (library_id, name) 为主键。四组各自 UPSERT 部分列,互不覆盖。

连接: psycopg3 ConnectionPool。参数来自 config.derived_postgres_*。
_init_schema() 首次幂等建表 (与 init SQL 等价,兜底裸库/迁移场景)。
"""
import json
from typing import Any

from psycopg_pool import ConnectionPool

from .base import DerivedStore, DerivedRecord


_SCHEMA = """
CREATE TABLE IF NOT EXISTS factor_derived (
    library_id TEXT NOT NULL,
    name TEXT NOT NULL,
    author TEXT,
    has_pnl BOOLEAN,
    dump_days INT,
    delay INT,
    ret DOUBLE PRECISION,
    shrp DOUBLE PRECISION,
    mdd DOUBLE PRECISION,
    tvr DOUBLE PRECISION,
    fitness DOUBLE PRECISION,
    metrics_updated_at TIMESTAMPTZ,
    fields JSONB,
    tables JSONB,
    datasources_updated_at TIMESTAMPTZ,
    max_bcorr DOUBLE PRECISION,
    max_bcorr_factor TEXT,
    bcorr_updated_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (library_id, name)
);
CREATE INDEX IF NOT EXISTS ix_fd_fields ON factor_derived USING GIN (fields);
CREATE INDEX IF NOT EXISTS ix_fd_tables ON factor_derived USING GIN (tables);
CREATE INDEX IF NOT EXISTS ix_fd_author ON factor_derived (library_id, author);
"""


class PostgresDerivedStore(DerivedStore):
    def __init__(self, conninfo: str, library_id: str):
        self.lib = library_id
        self.pool = ConnectionPool(conninfo, min_size=1, max_size=4, open=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with self.pool.connection() as conn:
            conn.execute(_SCHEMA)

    def get_all(self, author: str | None = None) -> dict[str, DerivedRecord]:
        sql = (
            "SELECT name, author, has_pnl, dump_days, delay, "
            "ret, shrp, mdd, tvr, fitness, fields, tables, "
            "max_bcorr, max_bcorr_factor "
            "FROM factor_derived WHERE library_id = %s"
        )
        params: list[Any] = [self.lib]
        if author is not None:
            sql += " AND author = %s"
            params.append(author)
        out: dict[str, DerivedRecord] = {}
        with self.pool.connection() as conn:
            for row in conn.execute(sql, params):
                (name, auth, has_pnl, dump_days, delay, ret, shrp, mdd, tvr,
                 fitness, fields, tables, max_bcorr, max_bcorr_factor) = row
                out[name] = DerivedRecord(
                    name=name, author=auth, has_pnl=has_pnl, dump_days=dump_days,
                    delay=delay, ret=ret, shrp=shrp, mdd=mdd, tvr=tvr, fitness=fitness,
                    fields=fields, tables=tables,
                    max_bcorr=max_bcorr, max_bcorr_factor=max_bcorr_factor,
                )
        return out

    def upsert_index(self, entries: dict[str, dict[str, Any]]) -> None:
        if not entries:
            return
        rows = [
            (self.lib, name, e.get("author"), e.get("has_pnl"),
             e.get("dump_days"), e.get("delay"))
            for name, e in entries.items()
        ]
        sql = (
            "INSERT INTO factor_derived "
            "(library_id, name, author, has_pnl, dump_days, delay, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, now()) "
            "ON CONFLICT (library_id, name) DO UPDATE SET "
            "author = EXCLUDED.author, has_pnl = EXCLUDED.has_pnl, "
            "dump_days = EXCLUDED.dump_days, delay = EXCLUDED.delay, "
            "updated_at = now()"
        )
        with self.pool.connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, rows)

    def upsert_metrics(self, name: str, m: dict[str, Any]) -> None:
        sql = (
            "INSERT INTO factor_derived "
            "(library_id, name, ret, shrp, mdd, tvr, fitness, metrics_updated_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, now(), now()) "
            "ON CONFLICT (library_id, name) DO UPDATE SET "
            "ret = EXCLUDED.ret, shrp = EXCLUDED.shrp, mdd = EXCLUDED.mdd, "
            "tvr = EXCLUDED.tvr, fitness = EXCLUDED.fitness, "
            "metrics_updated_at = now(), updated_at = now()"
        )
        with self.pool.connection() as conn:
            conn.execute(sql, (self.lib, name, m.get("ret"), m.get("shrp"),
                               m.get("mdd"), m.get("tvr"), m.get("fitness")))

    def upsert_datasources(self, name: str, fields: list[str], tables: list[str]) -> None:
        sql = (
            "INSERT INTO factor_derived "
            "(library_id, name, fields, tables, datasources_updated_at, updated_at) "
            "VALUES (%s, %s, %s, %s, now(), now()) "
            "ON CONFLICT (library_id, name) DO UPDATE SET "
            "fields = EXCLUDED.fields, tables = EXCLUDED.tables, "
            "datasources_updated_at = now(), updated_at = now()"
        )
        with self.pool.connection() as conn:
            conn.execute(sql, (self.lib, name, json.dumps(fields), json.dumps(tables)))

    def upsert_bcorr(self, name: str, max_bcorr: float, max_bcorr_factor: str) -> None:
        sql = (
            "INSERT INTO factor_derived "
            "(library_id, name, max_bcorr, max_bcorr_factor, bcorr_updated_at, updated_at) "
            "VALUES (%s, %s, %s, %s, now(), now()) "
            "ON CONFLICT (library_id, name) DO UPDATE SET "
            "max_bcorr = EXCLUDED.max_bcorr, max_bcorr_factor = EXCLUDED.max_bcorr_factor, "
            "bcorr_updated_at = now(), updated_at = now()"
        )
        with self.pool.connection() as conn:
            conn.execute(sql, (self.lib, name, max_bcorr, max_bcorr_factor))

    def delete(self, name: str) -> bool:
        with self.pool.connection() as conn:
            cur = conn.execute(
                "DELETE FROM factor_derived WHERE library_id = %s AND name = %s",
                (self.lib, name),
            )
            return cur.rowcount > 0
