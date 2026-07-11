"""PostgreSQL 实现 factor_info store.

DDL 不在本类执行(2026-07-09 滚出 __init__,factor-aggregate-plan 阶段 2):
schema 归 `ops/infra/schema.py::ensure_schemas`(FK 依赖序引导)+ 生产的
scripts/postgres 迁移;store 构造零副作用。
"""
from ops.infra.pg import get_pool, ts_in, ts_out
from ops.utils.clock import now_iso

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
                # ts_out 与 repository._row_to_factor 同一套边界转换:原先此处
                # 直接 isoformat 带 +08:00 后缀,repo.get 与 repo.find 拿到的
                # identity.created_at 格式不一致(收官核对项,2026-07-11)。
                created_at=ts_out(row[3]),
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
            # 缺省先补 now_iso(时间戳格式 SSOT)再统一过 ts_in(naive local
            # ISO 打上本地 tz 入库,与 state/snapshot store 同款,否则 PG 按
            # session 时区解释可能偏 8h)—— 单一路径,不再内联 datetime.now()。
            (info.name, info.author, info.discovery_method,
             ts_in(info.created_at or now_iso())),
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
                    created_at=ts_out(row[3]),
                )
                for row in rows
            ]
