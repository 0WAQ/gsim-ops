"""produce 分组 roster store(produce_group / produce_group_member 两表)。

组拓扑(roster/ordinal/muted)是语义真相 → PG;盘面上的 group.xml 与组目录
是 DB 的**派生物**(每次 sync 重新生成)。`member.factor` 刻意**不 FK**
factor_info:因子被 rm 时 roster 行不删(ordinal 即 checkpoint 腿序号,删行
会让后续腿序号移位 = 静默数据污染),只能置 muted ——
见 docs/design/factor-produce-groups.md。
"""
import re

from .pg_store import GroupMember, PostgresGroupStore, ProduceGroup, ProduceSingle

__all__ = ["GroupMember", "PostgresGroupStore", "ProduceGroup", "ProduceSingle",
           "default_group_store"]


def _swap_dbname(conninfo: str, dbname: str) -> str:
    """试点接线:roster 写库与因子读库分离(produce.grouped.roster_dbname 非空
    时)。conninfo 是 config 自拼的空格分隔串,dbname 段恒存在且值不含空格。"""
    return re.sub(r"(?:^| )dbname=\S+", f" dbname={dbname}",
                  conninfo, count=1).strip()


def default_group_store(config) -> PostgresGroupStore:
    """分组 roster 只有 PG 实现(json dev 后端不承载产线拓扑)。"""
    conninfo = getattr(config, "state_postgres_conninfo", None)
    if not conninfo:
        raise ValueError("produce 分组需要 Postgres 后端,但未配置 state.postgres")
    roster_db = getattr(config, "produce_grouped_roster_dbname", None)
    if roster_db:
        conninfo = _swap_dbname(conninfo, roster_db)
    return PostgresGroupStore(conninfo)
