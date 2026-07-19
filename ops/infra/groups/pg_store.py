"""PostgreSQL 实现 produce 分组 roster store(produce_group 两表)。

DDL 不在本类执行:schema 归 `ops/infra/schema.py::ensure_schemas`(引导序
追加在 info → state → snapshot 之后)+ 生产的 scripts/postgres 迁移;
store 构造零副作用。
"""
from dataclasses import dataclass

from ops.infra.pg import get_pool

_SCHEMA = """
CREATE TABLE IF NOT EXISTS produce_group (
    gid TEXT PRIMARY KEY,
    author TEXT NOT NULL,
    delay INT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_produce_group_status CHECK (status IN ('active', 'superseded'))
);
CREATE TABLE IF NOT EXISTS produce_group_member (
    gid TEXT NOT NULL REFERENCES produce_group(gid),
    factor TEXT NOT NULL,
    ordinal INT NOT NULL,
    muted BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (gid, factor),
    CONSTRAINT uq_pgm_gid_ordinal UNIQUE (gid, ordinal)
);
CREATE INDEX IF NOT EXISTS ix_pgm_factor ON produce_group_member(factor);
CREATE TABLE IF NOT EXISTS produce_single (
    factor TEXT PRIMARY KEY,
    author TEXT NOT NULL,
    admitted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


@dataclass(frozen=True)
class ProduceGroup:
    gid: str
    author: str
    delay: int
    status: str = "active"


@dataclass(frozen=True)
class GroupMember:
    gid: str
    factor: str
    ordinal: int
    muted: bool = False


@dataclass(frozen=True)
class ProduceSingle:
    """单产注册行(在产 per-factor 形态;pending(待产)无行 —— 待产纯推导)。"""
    factor: str
    author: str


class PostgresGroupStore:
    """produce_group 两表的 Postgres 实现。"""

    def __init__(self, conninfo: str):
        self.pool = get_pool(conninfo)

    def create_group(self, group: ProduceGroup, factors: list[str]) -> None:
        """建组 + roster 单事务落库。ordinal = 名单序(调用方保证已字典序 ——
        顺序即 checkpoint 腿序号,落库后永不改)。"""
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO produce_group (gid, author, delay) VALUES (%s, %s, %s)",
                (group.gid, group.author, group.delay))
            for ordinal, factor in enumerate(factors):
                conn.execute(
                    "INSERT INTO produce_group_member (gid, factor, ordinal)"
                    " VALUES (%s, %s, %s)",
                    (group.gid, factor, ordinal))

    def list_groups(self, active_only: bool = True) -> list[ProduceGroup]:
        sql = "SELECT gid, author, delay, status FROM produce_group"
        if active_only:
            sql += " WHERE status = 'active'"
        with self.pool.connection() as conn:
            rows = conn.execute(sql + " ORDER BY gid").fetchall()
        return [ProduceGroup(gid=r[0], author=r[1], delay=r[2], status=r[3])
                for r in rows]

    def members(self, gid: str) -> list[GroupMember]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT gid, factor, ordinal, muted FROM produce_group_member"
                " WHERE gid = %s ORDER BY ordinal", (gid,)).fetchall()
        return [GroupMember(gid=r[0], factor=r[1], ordinal=r[2], muted=r[3])
                for r in rows]

    def set_muted(self, gid: str, factors: set[str], muted: bool) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE produce_group_member SET muted = %s"
                " WHERE gid = %s AND factor = ANY(%s)",
                (muted, gid, list(factors)))

    def active_membership(self) -> dict[str, str]:
        """factor → gid(仅 active 组)—— sync 判"在不在组里"的唯一查询。"""
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT m.factor, m.gid FROM produce_group_member m"
                " JOIN produce_group g ON g.gid = m.gid"
                " WHERE g.status = 'active'").fetchall()
        return {r[0]: r[1] for r in rows}

    def supersede(self, gid: str) -> None:
        """重组时旧组下线(roster 行保留留痕,不复号)。"""
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE produce_group SET status = 'superseded' WHERE gid = %s",
                (gid,))

    # ------------------------------------------------------------------
    # 单产注册表(produce_single)
    # ------------------------------------------------------------------

    def admit_single(self, factor: str, author: str) -> None:
        """pending → single 准入(幂等:重复准入 = 刷新 admitted_at)。"""
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO produce_single (factor, author) VALUES (%s, %s)"
                " ON CONFLICT (factor) DO UPDATE SET"
                " author = EXCLUDED.author, admitted_at = now()",
                (factor, author))

    def remove_single(self, factor: str) -> None:
        """退回 pending(离 ACTIVE / 封组转正);删行即退,不留墓碑。"""
        with self.pool.connection() as conn:
            conn.execute("DELETE FROM produce_single WHERE factor = %s", (factor,))

    def list_singles(self) -> list[ProduceSingle]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT factor, author FROM produce_single ORDER BY factor").fetchall()
        return [ProduceSingle(factor=r[0], author=r[1]) for r in rows]
