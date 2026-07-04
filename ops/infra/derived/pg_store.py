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
def _metric_expr(prefix: str = "") -> dict[str, str]:
    """数值键 -> SQL 表达式,可选加表别名前缀 (JOIN 子查询消歧义用)。
    _METRIC_EXPR (无前缀,单表 get_all) 与 _METRIC_EXPR_D (d. 前缀,JOIN
    外层 ORDER BY) 同源生成,杜绝两处 drift。"""
    p = prefix
    return {
        "ret": f"{p}ret",
        "shrp": f"{p}shrp",
        "mdd": f"{p}mdd",
        "tvr": f"{p}tvr",
        "fitness": f"{p}fitness",
        "dump_days": f"COALESCE({p}dump_days, 0)",
        "delay": f"{p}delay",
        "bcorr": f"abs({p}max_bcorr)",
    }


_METRIC_EXPR = _metric_expr()
_METRIC_EXPR_D = _metric_expr("d.")

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

    _COL_NAMES = (
        "name", "author", "has_pnl", "dump_days", "delay",
        "ret", "shrp", "mdd", "tvr", "fitness", "fields", "tables",
        "max_bcorr", "max_bcorr_factor",
    )

    @classmethod
    def _cols(cls, prefix: str) -> str:
        """带表别名前缀的 SELECT 列串 (JOIN 里给 d. 消歧义)。列顺序与 _COLS /
        _row_to_record 严格一致。"""
        return ", ".join(f"{prefix}{c}" for c in cls._COL_NAMES)

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

    def _derived_where(
        self,
        author: str | None,
        field: str | None,
        table_glob: str | None,
        has_index: bool,
        metrics: list[tuple[str, str, float]] | None,
        prefix: str = "",
    ) -> tuple[list[str], list[Any]]:
        """把 get_all 的派生层谓词拆成 (clauses, params) —— get_all 与 join_state
        的 JOIN 子句共用同一套 WHERE,单一真相源,避免两处 drift。clauses[0] 恒为
        library_id 过滤;prefix 是列的表别名前缀 (JOIN 里传 'd.' 消歧义,单表 get_all
        传 '')。metric 表达式走 _metric_expr(prefix) 同源生成。"""
        p = prefix
        mexpr = _metric_expr(p)
        clauses = [f"{p}library_id = %s"]
        params: list[Any] = [self.lib]
        if author is not None:
            clauses.append(f"{p}author = %s")
            params.append(author)
        if has_index:
            # author 非空 == 有 index 组 == 在 alpha_src (list.py 恒施加的集合过滤)。
            clauses.append(f"{p}author IS NOT NULL")
        if field is not None:
            # fields @> '["X"]'::jsonb 命中 GIN 索引 ix_fd_fields
            clauses.append(f"{p}fields @> %s::jsonb")
            params.append(json.dumps([field]))
        if table_glob is not None:
            like = _glob_to_like(table_glob)
            if like is not None:
                # tables 不吃 GIN,但把 glob 过滤放 PG 端省传输;含 LIKE 无法
                # 表达的元字符时 like 为 None,跳过下推留给内存兜底。
                clauses.append(
                    f"EXISTS (SELECT 1 FROM jsonb_array_elements_text({p}tables) t "
                    "WHERE t LIKE %s)"
                )
                params.append(like)
        for key, op, threshold in metrics or []:
            expr = mexpr.get(key)
            if expr is None or op not in _SQL_OPS:
                continue  # 未知键/运算符跳过下推,靠内存兜底
            clauses.append(f"{expr} {op} %s")
            params.append(threshold)
        return clauses, params

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
        clauses, params = self._derived_where(author, field, table_glob, has_index, metrics)
        sql = f"SELECT {self._COLS} FROM factor_derived WHERE {' AND '.join(clauses)}"
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

    def join_state(
        self,
        author: str | None = None,
        *,
        field: str | None = None,
        table_glob: str | None = None,
        has_index: bool = False,
        metrics: list[tuple[str, str, float]] | None = None,
        status: str | None = None,
        sort_by: str | None = None,
        limit: int | None = None,
    ) -> list[tuple[DerivedRecord, str | None, str | None]]:
        """派生层 LEFT JOIN 状态层 (同库同 library_id),一次查回
        (DerivedRecord, status, last_fail_stage)。取代 list.py 里"读派生 + 读全表
        state + Python 按 name 合并"的两次读。

        - 驱动表是 factor_derived d (list 是 derived 集合驱动:has_index 恒过滤 d.author);
          factor_state s 走 LEFT JOIN,无 state 行的因子 status/last_fail_stage 为 None
          (对齐旧路径 state_records.get(name) 返回 None)。
        - status 给定则下推 s.status = %s (旧路径在 Python 里做,现进 SQL)。
        - 派生层谓词复用 _derived_where(prefix='d.'),与 get_all 单一真相源。

        注意: 这是本 store 唯一跨表处 —— 明知 factor_state 的 schema (status/
        last_fail_stage/library_id/name),换取 list 热路径一次 JOIN。仅 pg 后端可用
        (json 两个独立文件 JOIN 不成立,coordinator 走两次读兜底)。"""
        clauses, params = self._derived_where(
            author, field, table_glob, has_index, metrics, prefix="d."
        )
        # status 下推:LEFT JOIN 下 s.status 对无 state 行为 NULL,`= %s` 天然排除,
        # 与旧路径 `(s := state_records.get(name)) and s.status == args.status` 等价。
        if status is not None:
            clauses.append("s.status = %s")
            params.append(status)
        sql = (
            f"SELECT {self._cols('d.')}, s.status, s.last_fail_stage "
            "FROM factor_derived d "
            "LEFT JOIN factor_state s "
            "ON s.library_id = d.library_id AND s.name = d.name "
            f"WHERE {' AND '.join(clauses)}"
        )
        if sort_by is not None and sort_by in _METRIC_EXPR_D:
            sql += f" ORDER BY {_METRIC_EXPR_D[sort_by]} DESC NULLS LAST, d.name ASC"
        else:
            sql += " ORDER BY d.name ASC"
        if limit is not None:
            sql += " LIMIT %s"
            params.append(limit)
        out: list[tuple[DerivedRecord, str | None, str | None]] = []
        with self.pool.connection() as conn:
            for row in conn.execute(sql, params):
                rec = self._row_to_record(row[:-2])
                out.append((rec, row[-2], row[-1]))
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
