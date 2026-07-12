"""Postgres state 后端 —— 因子生命周期真相源.

从 Redis 迁入 (2026-07-04)。2026-07-06 重构: 去掉 library_id (永远单库) + author
(移到 factor_info), 主键改为自增 id + name UNIQUE。

实现 StateStore ABC,与 PostgresSnapshotStore 同范式 (psycopg3 ConnectionPool +
幂等 _init_schema)。

原子性: Redis 的 WATCH/MULTI/EXEC 乐观锁在 PG 里用事务 + `SELECT ... FOR UPDATE`
行级锁替代 —— transition/append_check 在一个事务内锁住目标行读改写,天然串行,
不需要应用层重试循环。

时间戳: FactorRecord 的时间戳字段是 ISO string (datetime.now().isoformat,naive
local)。PG 列是 TIMESTAMPTZ。转换只在本 store 读写边界发生:写时 string 直接入
(psycopg 解析),读时 datetime -> .isoformat(timespec="seconds") 转回 string 喂给
FactorRecord。check_history 存 JSONB,内部时间戳原样留在 json 里不动。
"""
from typing import Any

from psycopg.types.json import Jsonb

from ops.core.state import CheckRecord, FactorRecord, FactorStatus
from ops.infra.errors import FactorNotFound
from ops.infra.pg import get_pool
from ops.infra.pg import ts_in as _ts_in
from ops.infra.pg import ts_out as _ts_out

from .base import StateConflict, StateStore

_SCHEMA = """
CREATE TABLE IF NOT EXISTS factor_state (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    version INT NOT NULL DEFAULT 1,
    submitted_at TIMESTAMPTZ,
    entered_at TIMESTAMPTZ,
    rejected_at TIMESTAMPTZ,
    last_fail_stage TEXT,
    last_fail_reason TEXT,
    check_history JSONB NOT NULL DEFAULT '[]',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (name) REFERENCES factor_info(name) ON DELETE CASCADE,
    CONSTRAINT chk_status CHECK (status IN ('submitted', 'checking', 'active', 'rejected')),
    CONSTRAINT chk_active_entered CHECK (status <> 'active' OR entered_at IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ix_fs_status ON factor_state(status);
"""

# Column order shared by SELECT and row->record mapping.
_COLS = (
    "name, status, version, submitted_at, entered_at, "
    "rejected_at, last_fail_stage, last_fail_reason, "
    "check_history, updated_at"
)

# Scalar (non-timestamp, non-status, non-check) fields settable via transition().
_TS_FIELDS = {"submitted_at", "entered_at", "rejected_at", "updated_at"}


from ops.utils.clock import now_iso as _now  # 单一真相源, 见 utils/clock.py

# _ts_in/_ts_out 正主已收敛到 ops/infra/pg.py(ts_in/ts_out),此处只留别名 ——
# 与 snapshot/pg_store 的镜像自 2026-07-09 合并(repository 是第三个消费者)。


class PostgresStateStore(StateStore):
    def __init__(self, conninfo: str):
        """构造零副作用:DDL 归 ops/infra/schema.py::ensure_schemas(2026-07-09
        滚出 __init__)+ 生产 scripts/postgres 迁移。"""
        self.pool = get_pool(conninfo)

    def _row_to_record(self, row) -> FactorRecord:
        (name, status, version, submitted_at, entered_at,
         rejected_at, last_fail_stage, last_fail_reason,
         check_history, updated_at) = row
        checks = [CheckRecord.from_dict(c) for c in (check_history or [])]
        return FactorRecord(
            name=name,
            status=FactorStatus(status),
            updated_at=_ts_out(updated_at),
            submitted_at=_ts_out(submitted_at),
            entered_at=_ts_out(entered_at),
            rejected_at=_ts_out(rejected_at),
            last_fail_stage=last_fail_stage,
            last_fail_reason=last_fail_reason,
            version=version,
            check_history=checks,
        )

    def get(self, name: str) -> FactorRecord | None:
        sql = f"SELECT {_COLS} FROM factor_state WHERE name = %s"
        with self.pool.connection() as conn:
            row = conn.execute(sql, (name,)).fetchone()
            return self._row_to_record(row) if row else None

    @staticmethod
    def put_on(conn, record: FactorRecord, stamp: bool = True) -> None:
        """在调用方给定的连接/事务上执行 put —— repository.register 用它与
        factor_info 的 upsert 合进同一个事务(原子入库)。"""
        # stamp=False preserves record.updated_at as-is (used by migration to
        # keep the original Redis timestamp). Normal writes bump it to now.
        if stamp:
            record.updated_at = _now()
        checks = Jsonb([c.to_dict() for c in record.check_history])
        sql = (
            "INSERT INTO factor_state "
            "(name, status, version, submitted_at, "
            "entered_at, rejected_at, last_fail_stage, last_fail_reason, "
            "check_history, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (name) DO UPDATE SET "
            "status=EXCLUDED.status, version=EXCLUDED.version, "
            "submitted_at=EXCLUDED.submitted_at, "
            "entered_at=EXCLUDED.entered_at, rejected_at=EXCLUDED.rejected_at, "
            "last_fail_stage=EXCLUDED.last_fail_stage, "
            "last_fail_reason=EXCLUDED.last_fail_reason, "
            "check_history=EXCLUDED.check_history, updated_at=EXCLUDED.updated_at"
        )
        conn.execute(sql, (
            record.name, record.status.value, record.version,
            _ts_in(record.submitted_at),
            _ts_in(record.entered_at), _ts_in(record.rejected_at),
            record.last_fail_stage, record.last_fail_reason,
            checks, _ts_in(record.updated_at),
        ))

    def put(self, record: FactorRecord, stamp: bool = True) -> None:
        with self.pool.connection() as conn:
            self.put_on(conn, record, stamp=stamp)

    def list(self, status: FactorStatus | None = None) -> list[FactorRecord]:
        """列出所有因子状态，可按 status 过滤。

        author 过滤已移除（author 在 factor_info 表，需要 JOIN）。
        """
        sql = f"SELECT {_COLS} FROM factor_state"
        params: list[Any] = []
        if status is not None:
            sql += " WHERE status = %s"
            params.append(status.value)
        with self.pool.connection() as conn:
            return [self._row_to_record(r) for r in conn.execute(sql, params)]

    def transition(self, name: str, to_status: FactorStatus,
                   expect: FactorStatus | None = None, **updates) -> FactorRecord:
        sql = f"SELECT {_COLS} FROM factor_state WHERE name = %s FOR UPDATE"
        with self.pool.connection() as conn:
            with conn.transaction():
                row = conn.execute(sql, (name,)).fetchone()
                if row is None:
                    raise FactorNotFound(f"factor not found: {name}")
                rec = self._row_to_record(row)
                if expect is not None and rec.status != expect:
                    # FOR UPDATE 行锁内的 CAS —— 并发安全的 from-status 守卫
                    raise StateConflict(
                        f"{name}: status={rec.status.value}, expect={expect.value}")
                rec.status = to_status
                for k, v in updates.items():
                    setattr(rec, k, v)
                rec.updated_at = _now()
                conn.execute(
                    "UPDATE factor_state SET status=%s, version=%s, "
                    "submitted_at=%s, entered_at=%s, rejected_at=%s, "
                    "last_fail_stage=%s, last_fail_reason=%s, updated_at=%s "
                    "WHERE name=%s",
                    (rec.status.value, rec.version,
                     _ts_in(rec.submitted_at),
                     _ts_in(rec.entered_at), _ts_in(rec.rejected_at),
                     rec.last_fail_stage, rec.last_fail_reason, _ts_in(rec.updated_at),
                     name),
                )
                return rec

    def append_check(self, name: str, check: CheckRecord) -> None:
        one = Jsonb([check.to_dict()])
        now = _ts_in(_now())
        with self.pool.connection() as conn:
            with conn.transaction():
                # Lock the row so a concurrent delete can't slip between the
                # existence check and the append.
                row = conn.execute(
                    "SELECT 1 FROM factor_state WHERE name=%s FOR UPDATE",
                    (name,),
                ).fetchone()
                if row is None:
                    raise FactorNotFound(f"factor not found: {name}")
                conn.execute(
                    "UPDATE factor_state SET check_history = check_history || %s, "
                    "updated_at = %s WHERE name=%s",
                    (one, now, name),
                )

    def delete(self, name: str) -> bool:
        with self.pool.connection() as conn:
            cur = conn.execute(
                "DELETE FROM factor_state WHERE name=%s",
                (name,),
            )
            return cur.rowcount > 0
