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


def _glob_to_like(glob: str) -> str | None:
    """把 fnmatch glob 转成 SQL LIKE 模式。只处理 * 和 ?;含字符类 ([...])
    等 LIKE 无法等价表达的元字符时返回 None (调用方跳过下推,靠内存兜底)。
    先转义 LIKE 自身的元字符 % 和 _,再把 glob 的 * -> %,? -> _。"""
    if "[" in glob or "]" in glob:
        return None
    out = glob.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return out.replace("*", "%").replace("?", "_")


# 数值键 -> SQL 表达式,用于 metric 阈值过滤和排序下推。必须逐键镜像
# base.metric_get / sort_key 的 Python 语义 (三处不能 drift):
#   bcorr 用 abs(max_bcorr);dump_days 排序 None->0 故 COALESCE。
# 过滤时 `<expr> <op> %s` 对 NULL 行天然假 (排除),与 metric_get 的 None 排除一致;
# 排序 `<expr> DESC NULLS LAST` 把 NULL 排最后,与 sort_key 的 -inf 一致
# (dump_days 走 COALESCE 后无 NULL,与 None->0 一致)。
_METRIC_EXPR = {
    "ret": "ret",
    "shrp": "shrp",
    "mdd": "mdd",
    "tvr": "tvr",
    "fitness": "fitness",
    "dump_days": "COALESCE(dump_days, 0)",
    "delay": "delay",
    "bcorr": "abs(max_bcorr)",
}

# SQL 比较运算符白名单 (防注入:op 直接拼进 SQL,必须来自固定集合)。
_SQL_OPS = {">", ">=", "<", "<=", "="}


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
CREATE TABLE IF NOT EXISTS derived_meta (
    library_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    PRIMARY KEY (library_id, key)
);
"""


class PostgresDerivedStore(DerivedStore):
    def __init__(self, conninfo: str, library_id: str):
        self.lib = library_id
        self.pool = ConnectionPool(conninfo, min_size=1, max_size=4, open=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with self.pool.connection() as conn:
            conn.execute(_SCHEMA)

    _COLS = (
        "name, author, has_pnl, dump_days, delay, "
        "ret, shrp, mdd, tvr, fitness, fields, tables, "
        "max_bcorr, max_bcorr_factor"
    )

    @staticmethod
    def _row_to_record(row) -> DerivedRecord:
        (name, auth, has_pnl, dump_days, delay, ret, shrp, mdd, tvr,
         fitness, fields, tables, max_bcorr, max_bcorr_factor) = row
        return DerivedRecord(
            name=name, author=auth, has_pnl=has_pnl, dump_days=dump_days,
            delay=delay, ret=ret, shrp=shrp, mdd=mdd, tvr=tvr, fitness=fitness,
            fields=fields, tables=tables,
            max_bcorr=max_bcorr, max_bcorr_factor=max_bcorr_factor,
        )

    def get_all(
        self,
        author: str | None = None,
        *,
        field: str | None = None,
        table_glob: str | None = None,
        has_index: bool = False,
        metrics: list[tuple[str, str, float]] | None = None,
        sort_by: str | None = None,
        limit: int | None = None,
    ) -> dict[str, DerivedRecord]:
        sql = f"SELECT {self._COLS} FROM factor_derived WHERE library_id = %s"
        params: list[Any] = [self.lib]
        if author is not None:
            sql += " AND author = %s"
            params.append(author)
        if has_index:
            # author 非空 == 有 index 组 == 在 alpha_src (list.py 恒施加的集合过滤)。
            sql += " AND author IS NOT NULL"
        if field is not None:
            # fields @> '["X"]'::jsonb 命中 GIN 索引 ix_fd_fields
            sql += " AND fields @> %s::jsonb"
            params.append(json.dumps([field]))
        if table_glob is not None:
            like = _glob_to_like(table_glob)
            if like is not None:
                # tables 不吃 GIN,但把 glob 过滤放 PG 端省传输;含 LIKE 无法
                # 表达的元字符时 like 为 None,跳过下推留给内存兜底。
                sql += (
                    " AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(tables) t "
                    "WHERE t LIKE %s)"
                )
                params.append(like)
        for key, op, threshold in metrics or []:
            expr = _METRIC_EXPR.get(key)
            if expr is None or op not in _SQL_OPS:
                continue  # 未知键/运算符跳过下推,靠内存兜底
            sql += f" AND {expr} {op} %s"
            params.append(threshold)
        # 排序下推:sort_by 给定则 <expr> DESC NULLS LAST, name ASC (name 二级序
        # 对齐 list.py 的 stable sort tie-break);否则默认 name ASC。
        if sort_by is not None and sort_by in _METRIC_EXPR:
            sql += f" ORDER BY {_METRIC_EXPR[sort_by]} DESC NULLS LAST, name ASC"
        else:
            sql += " ORDER BY name ASC"
        if limit is not None:
            sql += " LIMIT %s"
            params.append(limit)
        out: dict[str, DerivedRecord] = {}
        with self.pool.connection() as conn:
            for row in conn.execute(sql, params):
                rec = self._row_to_record(row)
                out[rec.name] = rec
        return out

    def get(self, name: str) -> DerivedRecord | None:
        sql = f"SELECT {self._COLS} FROM factor_derived WHERE library_id = %s AND name = %s"
        with self.pool.connection() as conn:
            row = conn.execute(sql, (self.lib, name)).fetchone()
            return self._row_to_record(row) if row else None

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

    def get_meta(self, key: str) -> str | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT value FROM derived_meta WHERE library_id = %s AND key = %s",
                (self.lib, key),
            ).fetchone()
            return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO derived_meta (library_id, key, value) VALUES (%s, %s, %s) "
                "ON CONFLICT (library_id, key) DO UPDATE SET value = EXCLUDED.value",
                (self.lib, key, value),
            )
