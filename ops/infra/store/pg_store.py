"""Postgres state 后端 —— 因子生命周期真相源.

schema: 去掉 library_id (永远单库) + author (移到 factor_info), 主键是自增
id + name UNIQUE。

原子性: 事务 + `SELECT ... FOR UPDATE` 行级锁 —— transition/append_check 在一个
事务内锁住目标行读改写,天然串行,不需要应用层重试循环。

时间戳: FactorRecord 的时间戳字段是 ISO string (datetime.now().isoformat,naive
local)。PG 列是 TIMESTAMPTZ。转换只在本 store 读写边界发生:写时 string 直接入
(psycopg 解析),读时 datetime -> .isoformat(timespec="seconds") 转回 string 喂给
FactorRecord。

factor_history 全操作审计表(本模块持有其 DDL 与发射函数 emit_on):
check_history JSONB 与 rejected_at/last_fail_stage/last_fail_reason 三列退役,
事实迁此表。"一次操作是一条记录",且历史活过 ops rm(无 FK)。get() 的
check_history 从事件表组装;"最近失败"走 last_fail() 派生查询。
"""
from typing import Any

from ops.core.state import CheckRecord, FactorRecord, FactorStatus, HistoryEvent
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
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (name) REFERENCES factor_info(name) ON DELETE CASCADE,
    CONSTRAINT chk_status CHECK (status IN ('submitted', 'checking', 'active', 'rejected')),
    CONSTRAINT chk_active_entered CHECK (status <> 'active' OR entered_at IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ix_fs_status ON factor_state(status);
CREATE TABLE IF NOT EXISTS factor_history (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL,
    op TEXT NOT NULL,
    at TIMESTAMPTZ NOT NULL,
    actor TEXT,
    started_at TIMESTAMPTZ,
    passed BOOLEAN,
    failed_stage TEXT,
    fail_reason TEXT,
    CONSTRAINT chk_op CHECK (op IN ('submit', 'overwrite', 'check', 'approve', 'restage', 'cancel', 'rm', 'backfill', 'entered')),
    CONSTRAINT chk_fail_has_stage CHECK (passed IS DISTINCT FROM FALSE OR failed_stage IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ix_fh_name_at ON factor_history(name, at DESC);
"""

# Column order shared by SELECT and row->record mapping.
_COLS = "name, status, version, submitted_at, entered_at, updated_at"

_EVENT_COLS = "name, op, at, actor, started_at, passed, failed_stage, fail_reason"


from ops.utils.clock import now_iso as _now  # 单一真相源, 见 utils/clock.py

# _ts_in/_ts_out 正主已收敛到 ops/infra/pg.py(ts_in/ts_out),此处只留别名。


def emit_on(conn, event: HistoryEvent) -> None:
    """在调用方给定的连接/事务上发射一条 factor_history 事件。

    唯一 INSERT 口:store 的 transition/append_check/delete 与
    repository.register 都经此写事件,保证与业务写同事务(要么都落、要么
    都不落,不存在"改了状态没记账"的半截)。
    """
    conn.execute(
        f"INSERT INTO factor_history ({_EVENT_COLS}) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (event.name, event.op, _ts_in(event.at), event.actor,
         _ts_in(event.started_at), event.passed,
         event.failed_stage, event.fail_reason),
    )


def _row_to_event(row) -> HistoryEvent:
    name, op, at, actor, started_at, passed, failed_stage, fail_reason = row
    return HistoryEvent(
        name=name, op=op, at=_ts_out(at) or "", actor=actor,  # at 列 NOT NULL
        started_at=_ts_out(started_at), passed=passed,
        failed_stage=failed_stage, fail_reason=fail_reason,
    )


class PostgresStateStore(StateStore):
    def __init__(self, conninfo: str):
        """构造零副作用:DDL 归 ops/infra/schema.py::ensure_schemas + 生产
        scripts/postgres 迁移。"""
        self.pool = get_pool(conninfo)

    def _row_to_record(self, row) -> FactorRecord:
        name, status, version, submitted_at, entered_at, updated_at = row
        return FactorRecord(
            name=name,
            status=FactorStatus(status),
            updated_at=_ts_out(updated_at),
            submitted_at=_ts_out(submitted_at),
            entered_at=_ts_out(entered_at),
            version=version,
        )

    def checks(self, name: str) -> "list[CheckRecord]":
        """check 全史:从事件表组装(op='check',at 升序;v2c 自 record 剥离,
        按需查)。CheckRecord.finished_at ← 事件 at(发射时 at=finished_at|now)。"""
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_EVENT_COLS} FROM factor_history "
                "WHERE name = %s AND op = 'check' ORDER BY at, id",
                (name,),
            ).fetchall()
        out = []
        for r in rows:
            e = _row_to_event(r)
            out.append(CheckRecord(
                started_at=e.started_at or e.at,
                finished_at=e.at,
                passed=e.passed,
                failed_stage=e.failed_stage,
                fail_reason=e.fail_reason,
            ))
        return out

    def get(self, name: str) -> FactorRecord | None:
        sql = f"SELECT {_COLS} FROM factor_state WHERE name = %s"
        with self.pool.connection() as conn:
            row = conn.execute(sql, (name,)).fetchone()
            return self._row_to_record(row) if row else None

    @staticmethod
    def put_on(conn, record: FactorRecord, stamp: bool = True) -> None:
        """在调用方给定的连接/事务上执行 put —— repository.register 用它与
        factor_info 的 upsert 合进同一个事务(原子入库)。
        注:check 史在事件表(v2c 起 record 无 check_history 字段)。"""
        # stamp=False preserves record.updated_at as-is (used by migration to
        # keep the original Redis timestamp). Normal writes bump it to now.
        if stamp:
            record.updated_at = _now()
        sql = (
            "INSERT INTO factor_state "
            "(name, status, version, submitted_at, entered_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (name) DO UPDATE SET "
            "status=EXCLUDED.status, version=EXCLUDED.version, "
            "submitted_at=EXCLUDED.submitted_at, "
            "entered_at=EXCLUDED.entered_at, updated_at=EXCLUDED.updated_at"
        )
        conn.execute(sql, (
            record.name, record.status.value, record.version,
            _ts_in(record.submitted_at), _ts_in(record.entered_at),
            _ts_in(record.updated_at),
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
                   expect: FactorStatus | None = None,
                   op: str | None = None, actor: str | None = None,
                   **updates) -> FactorRecord:
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
                    "submitted_at=%s, entered_at=%s, updated_at=%s "
                    "WHERE name=%s",
                    (rec.status.value, rec.version,
                     _ts_in(rec.submitted_at), _ts_in(rec.entered_at),
                     _ts_in(rec.updated_at), name),
                )
                # 事件同事务发射:命令 op(approve/restage/overwrite...)+
                # 'entered' 自动标记(置 ACTIVE 即入库,三径合流,见 base.py)
                if op is not None:
                    emit_on(conn, HistoryEvent(
                        name=name, op=op, at=rec.updated_at, actor=actor))
                if to_status == FactorStatus.ACTIVE:
                    emit_on(conn, HistoryEvent(
                        name=name, op="entered", at=rec.updated_at, actor=actor))
                return rec

    def append_check(self, name: str, check: CheckRecord,
                     actor: str | None = None) -> None:
        now = _now()
        with self.pool.connection() as conn:
            with conn.transaction():
                # Lock the row so a concurrent delete can't slip between the
                # existence check and the event insert.
                row = conn.execute(
                    "SELECT 1 FROM factor_state WHERE name=%s FOR UPDATE",
                    (name,),
                ).fetchone()
                if row is None:
                    raise FactorNotFound(f"factor not found: {name}")
                emit_on(conn, HistoryEvent(
                    name=name, op="check",
                    at=check.finished_at or now, actor=actor,
                    started_at=check.started_at, passed=check.passed,
                    failed_stage=check.failed_stage,
                    fail_reason=check.fail_reason,
                ))
                conn.execute(
                    "UPDATE factor_state SET updated_at = %s WHERE name=%s",
                    (_ts_in(now), name),
                )

    def delete(self, name: str, op: str | None = None,
               actor: str | None = None) -> bool:
        with self.pool.connection() as conn:
            with conn.transaction():
                cur = conn.execute(
                    "DELETE FROM factor_state WHERE name=%s",
                    (name,),
                )
                if cur.rowcount > 0 and op is not None:
                    emit_on(conn, HistoryEvent(
                        name=name, op=op, at=_now(), actor=actor))
                return cur.rowcount > 0

    def last_fail(self, name: str) -> HistoryEvent | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                f"SELECT {_EVENT_COLS} FROM factor_history "
                "WHERE name = %s AND op = 'check' AND passed = FALSE "
                "ORDER BY at DESC, id DESC LIMIT 1",
                (name,),
            ).fetchone()
            return _row_to_event(row) if row else None

    def latest_check_ats(self) -> "dict[str, str]":
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT ON (name) name, at FROM factor_history "
                "WHERE op = 'check' ORDER BY name, at DESC, id DESC",
            ).fetchall()
            return {r[0]: _ts_out(r[1]) or "" for r in rows}

    def history(self, name: str) -> "list[HistoryEvent]":  # 引号防 list 方法遮蔽
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_EVENT_COLS} FROM factor_history "
                "WHERE name = %s ORDER BY at, id",
                (name,),
            ).fetchall()
            return [_row_to_event(r) for r in rows]
