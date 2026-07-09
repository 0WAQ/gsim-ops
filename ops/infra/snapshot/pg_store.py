"""PostgreSQL 实现 factor_snapshot store."""
from datetime import datetime

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .base import FactorSnapshot, SnapshotStore


def _ts_in(v: str | None) -> str | None:
    """Naive local ISO string -> TIMESTAMPTZ-ready value (与 state_store 一致)。"""
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        return v
    if dt.tzinfo is None:
        dt = dt.astimezone()  # 打上本地时区
    return dt.isoformat(timespec="seconds")


def _ts_out(v) -> str | None:
    """TIMESTAMPTZ -> naive local ISO string (与 state_store 一致)。"""
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is not None:
            v = v.astimezone().replace(tzinfo=None)
        return v.isoformat(timespec="seconds")
    return str(v)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS factor_snapshot (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,

    ret DOUBLE PRECISION,
    shrp DOUBLE PRECISION,
    mdd DOUBLE PRECISION,
    tvr DOUBLE PRECISION,
    fitness DOUBLE PRECISION,

    fields JSONB,
    tables JSONB,

    delay INT,

    max_bcorr DOUBLE PRECISION,
    max_bcorr_factor TEXT,

    snapshot_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (name) REFERENCES factor_info(name) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_factor_snapshot_fields ON factor_snapshot USING GIN(fields);
CREATE INDEX IF NOT EXISTS idx_factor_snapshot_tables ON factor_snapshot USING GIN(tables);
CREATE INDEX IF NOT EXISTS idx_factor_snapshot_ret ON factor_snapshot(ret);
CREATE INDEX IF NOT EXISTS idx_factor_snapshot_shrp ON factor_snapshot(shrp);
"""

# Metric 键到 SQL 表达式的映射（复用原 DerivedStore 逻辑）
_METRIC_EXPR = {
    "ret": "ret",
    "shrp": "shrp",
    "mdd": "mdd",
    "tvr": "tvr",
    "fitness": "fitness",
    "bcorr": "abs(max_bcorr)",
}

_SQL_OPS = {"<": "<", ">": ">", "=": "=", "<=": "<=", ">=": ">="}


class PostgresSnapshotStore(SnapshotStore):
    """factor_snapshot 表的 Postgres 实现（入库时快照，不可变）。"""

    def __init__(self, conninfo: str):
        self.pool = ConnectionPool(conninfo, min_size=1, max_size=10, open=True)
        self._init_schema()

    def _init_schema(self):
        with self.pool.connection() as conn:
            conn.execute(_SCHEMA)

    def get(self, name: str) -> FactorSnapshot | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                """
                SELECT name, ret, shrp, mdd, tvr, fitness,
                       fields, tables, delay,
                       max_bcorr, max_bcorr_factor, snapshot_at
                FROM factor_snapshot WHERE name = %s
                """,
                (name,),
            ).fetchone()
            if not row:
                return None
            return self._row_to_snapshot(row)

    def insert(self, snapshot: FactorSnapshot) -> None:
        """插入快照（check 通过时）。如果已存在则报错。"""
        with self.pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO factor_snapshot (
                    name, ret, shrp, mdd, tvr, fitness,
                    fields, tables, delay,
                    max_bcorr, max_bcorr_factor, snapshot_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    snapshot.name,
                    snapshot.ret, snapshot.shrp, snapshot.mdd, snapshot.tvr, snapshot.fitness,
                    Jsonb(snapshot.fields) if snapshot.fields else None,
                    Jsonb(snapshot.tables) if snapshot.tables else None,
                    snapshot.delay,
                    snapshot.max_bcorr, snapshot.max_bcorr_factor,
                    _ts_in(snapshot.snapshot_at),  # 关键：转换时区
                ),
            )

    def delete(self, name: str) -> None:
        with self.pool.connection() as conn:
            conn.execute("DELETE FROM factor_snapshot WHERE name = %s", (name,))

    def list(
        self,
        *,
        field: str | None = None,
        table_glob: str | None = None,
        metrics: list[tuple[str, str, float]] | None = None,
        sort_by: str | None = None,
        limit: int | None = None,
    ) -> dict[str, FactorSnapshot]:
        """列出快照，支持下推过滤（复用原 DerivedStore.get_all 逻辑）。"""
        where_clauses = []
        params = []

        if field:
            where_clauses.append("fields @> %s")
            params.append(Jsonb([field]))

        if table_glob:
            like_pattern = table_glob.replace("*", "%")
            where_clauses.append(
                "EXISTS (SELECT 1 FROM jsonb_array_elements_text(tables) t WHERE t LIKE %s)"
            )
            params.append(like_pattern)

        if metrics:
            for key, op, threshold in metrics:
                expr = _METRIC_EXPR.get(key)
                sql_op = _SQL_OPS.get(op)
                if expr and sql_op:
                    where_clauses.append(f"{expr} {sql_op} %s")
                    params.append(threshold)

        where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"

        order_by_sql = ""
        if sort_by:
            expr = _METRIC_EXPR.get(sort_by)
            if expr:
                order_by_sql = f"ORDER BY {expr} DESC NULLS LAST"

        # LIMIT 参数化(原 f-string 拼接是注入面/负数崩溃点,full-review 第三部分)
        limit_sql = ""
        if limit:
            limit_sql = "LIMIT %s"
            params.append(limit)

        query = f"""
            SELECT name, ret, shrp, mdd, tvr, fitness,
                   fields, tables, delay,
                   max_bcorr, max_bcorr_factor, snapshot_at
            FROM factor_snapshot
            WHERE {where_sql}
            {order_by_sql}
            {limit_sql}
        """

        with self.pool.connection() as conn:
            # 动态部分 (where/order/limit 结构) 全部来自白名单表达式,值走参数;
            # psycopg stub 要求 LiteralString,此处结构安全,定点豁免。
            rows = conn.execute(query, params).fetchall()  # pyright: ignore[reportArgumentType]

        return {row[0]: self._row_to_snapshot(row) for row in rows}

    def _row_to_snapshot(self, row) -> FactorSnapshot:
        return FactorSnapshot(
            name=row[0],
            ret=row[1],
            shrp=row[2],
            mdd=row[3],
            tvr=row[4],
            fitness=row[5],
            fields=row[6] if row[6] else None,
            tables=row[7] if row[7] else None,
            delay=row[8],
            max_bcorr=row[9],
            max_bcorr_factor=row[10],
            snapshot_at=_ts_out(row[11]),
        )
