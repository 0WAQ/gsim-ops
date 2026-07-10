"""PostgreSQL 实现 factor_info store.

DDL 不在本类执行(2026-07-09 滚出 __init__,factor-aggregate-plan 阶段 2):
schema 归 `ops/infra/schema.py::ensure_schemas`(FK 依赖序引导)+ 生产的
scripts/postgres 迁移;store 构造零副作用。
"""
from datetime import datetime

from ops.infra.pg import get_pool

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

    @staticmethod
    def upsert_on(conn, info: FactorInfo) -> None:
        """在调用方给定的连接/事务上执行 upsert —— repository.register 用它把
        info+state 双表写合进同一个事务(原子入库,submit/backfill/check 三份
        手抄编排的收编点)。"""
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

    def upsert(self, info: FactorInfo) -> None:
        with self.pool.connection() as conn:
            self.upsert_on(conn, info)

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
