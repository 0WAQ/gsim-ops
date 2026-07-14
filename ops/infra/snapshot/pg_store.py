"""PostgreSQL 实现 factor_snapshot store.

DDL 不在本类执行:schema 归 `ops/infra/schema.py::ensure_schemas` + 生产
scripts/postgres 迁移。_ts_in/_ts_out 正主收敛到 ops/infra/pg.py。

fields/tables 是 TEXT[]:psycopg 原生 list 适配零包裹、GIN(array_ops)吃
`@>` 包含查询、glob 经 unnest+LIKE。
"""
from ops.core.metrics import SNAPSHOT_METRICS
from ops.infra.pg import get_pool
from ops.infra.pg import ts_in as _ts_in
from ops.infra.pg import ts_out as _ts_out

from .base import FactorSnapshot, SnapshotStore

_SCHEMA = """
CREATE TABLE IF NOT EXISTS factor_snapshot (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,

    ret DOUBLE PRECISION,
    shrp DOUBLE PRECISION,
    mdd DOUBLE PRECISION,
    tvr DOUBLE PRECISION,
    fitness DOUBLE PRECISION,

    fields TEXT[],
    tables TEXT[],

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

_SQL_OPS = {"<": "<", ">": ">", "=": "=", "<=": "<=", ">=": ">="}


def _prefixed_metric_expr(key: str, prefix: str) -> str | None:
    """metric 键 → 带列前缀的 SQL 表达式(prefix 形如 "n.",JOIN 场景用)。

    键集与取值语义(bcorr = abs(max_bcorr))从 core/metrics.SNAPSHOT_METRICS
    派生 —— SQL 表达式与 list.py 的内存取值都长自注册表,别在此处另抄一份。"""
    spec = SNAPSHOT_METRICS.get(key)
    if spec is None:
        return None
    col = f"{prefix}{spec.column}"
    return f"abs({col})" if spec.absolute else col


def _glob_to_like(glob: str) -> str | None:
    """fnmatch glob → LIKE pattern;LIKE 表达不了时返回 None(跳过下推)。

    下推纯为预筛:LIKE 结果集只许 ⊇ fnmatch 精确语义,不许更窄(否则行在
    SQL 层被丢、内存 fnmatch 兜底永远看不到)。故须转义 glob 里的 `\\ % _`
    (不转义会变成 LIKE 通配或字面量,预筛可能更窄);`*`→`%`、`?`→`_`;
    含 `[`(字符类,LIKE 无法表达)整体放弃下推,交给内存 fnmatch。
    """
    if "[" in glob:
        return None
    like = (glob.replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
                .replace("*", "%")
                .replace("?", "_"))
    return like


def snapshot_where(
    field: str | None,
    table_glob: str | None,
    metrics: list[tuple[str, str, float]] | None,
    *,
    prefix: str = "",
) -> tuple[list[str], list]:
    """snapshot 侧过滤条件 → (WHERE 片段列表, 参数列表)。

    list()(单表,prefix="")与 repository.find(三表 JOIN,prefix="n.")共用,
    保证下推语义只有一份(fields GIN 包含 / tables glob→LIKE / metric 阈值)。
    下推是**预筛**:结果集只许 ⊇ 精确语义,不许更窄(内存侧兜底只能收窄)。
    """
    clauses: list[str] = []
    params: list = []

    if field:
        clauses.append(f"{prefix}fields @> %s")
        params.append([field])  # TEXT[] 包含:psycopg list → array

    if table_glob:
        like_pattern = _glob_to_like(table_glob)
        if like_pattern is not None:
            clauses.append(
                f"EXISTS (SELECT 1 FROM unnest({prefix}tables) t "
                "WHERE t LIKE %s)"
            )
            params.append(like_pattern)

    if metrics:
        for key, op, threshold in metrics:
            expr = _prefixed_metric_expr(key, prefix)
            sql_op = _SQL_OPS.get(op)
            if expr and sql_op:
                clauses.append(f"{expr} {sql_op} %s")
                params.append(threshold)

    return clauses, params


def metric_order_expr(sort_by: str | None, *, prefix: str = "") -> str | None:
    """sort 键 → ORDER BY 用的 SQL 表达式(白名单外返回 None)。"""
    if not sort_by:
        return None
    return _prefixed_metric_expr(sort_by, prefix)


class PostgresSnapshotStore(SnapshotStore):
    """factor_snapshot 表的 Postgres 实现（入库时快照，不可变）。"""

    def __init__(self, conninfo: str):
        self.pool = get_pool(conninfo)

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
                    snapshot.fields if snapshot.fields else None,
                    snapshot.tables if snapshot.tables else None,
                    snapshot.delay,
                    snapshot.max_bcorr, snapshot.max_bcorr_factor,
                    _ts_in(snapshot.snapshot_at),  # 关键：转换时区
                ),
            )

    def delete(self, name: str) -> bool:
        with self.pool.connection() as conn:
            cur = conn.execute("DELETE FROM factor_snapshot WHERE name = %s", (name,))
            return cur.rowcount > 0

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
        where_clauses, params = snapshot_where(field, table_glob, metrics)
        where_sql = " AND ".join(where_clauses) if where_clauses else "TRUE"

        order_by_sql = ""
        order_expr = metric_order_expr(sort_by)
        if order_expr:
            order_by_sql = f"ORDER BY {order_expr} DESC NULLS LAST"

        # LIMIT 参数化:原 f-string 拼接是注入面/负数崩溃点
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
