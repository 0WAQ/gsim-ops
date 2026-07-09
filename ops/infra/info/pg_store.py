"""PostgreSQL 实现 factor_info store."""
from datetime import datetime

from ops.infra.pg import ensure_schema, get_pool

from .base import FactorInfo, InfoStore

_SCHEMA = """
CREATE TABLE IF NOT EXISTS factor_info (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    author TEXT,
    discovery_method TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_factor_info_author ON factor_info(author);
CREATE INDEX IF NOT EXISTS idx_factor_info_discovery ON factor_info(discovery_method);
"""


class PostgresInfoStore(InfoStore):
    """factor_info 表的 Postgres 实现。"""

    def __init__(self, conninfo: str):
        self.pool = get_pool(conninfo)
        self._init_schema()

    def _init_schema(self):
        """幂等创建表和索引(每个 pool 只跑一次)。"""
        ensure_schema(self.pool, _SCHEMA)

    def get(self, name: str) -> FactorInfo | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT name, author, discovery_method, created_at FROM factor_info WHERE name = %s",
                (name,),
            ).fetchone()
            if not row:
                return None
            return FactorInfo(
                name=row[0],
                author=row[1],
                discovery_method=row[2],
                created_at=row[3].isoformat(timespec="seconds") if row[3] else None,
            )

    def upsert(self, info: FactorInfo) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO factor_info (name, author, discovery_method, created_at)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (name) DO UPDATE SET
                    author = EXCLUDED.author,
                    discovery_method = EXCLUDED.discovery_method
                """,
                (info.name, info.author, info.discovery_method, info.created_at or datetime.now()),
            )

    def delete(self, name: str) -> bool:
        with self.pool.connection() as conn:
            cur = conn.execute("DELETE FROM factor_info WHERE name = %s", (name,))
            return cur.rowcount > 0

    def list(self, author: str | None = None) -> list[FactorInfo]:
        with self.pool.connection() as conn:
            if author:
                rows = conn.execute(
                    "SELECT name, author, discovery_method, created_at FROM factor_info WHERE author = %s",
                    (author,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT name, author, discovery_method, created_at FROM factor_info"
                ).fetchall()

            return [
                FactorInfo(
                    name=row[0],
                    author=row[1],
                    discovery_method=row[2],
                    created_at=row[3].isoformat(timespec="seconds") if row[3] else None,
                )
                for row in rows
            ]
