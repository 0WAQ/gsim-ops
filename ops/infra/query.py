"""跨 state + derived 的联合读 (list / health 热路径).

派生层 (factor_derived) 与状态层 (factor_state) 现在同库 (Postgres, host 15432),
可一条 LEFT JOIN 查回 "因子派生数据 + 生命周期状态"。本模块是唯一知道两张表能拼
一起的地方 —— store/ 与 derived/ 仍各管各的单表,不互相耦合;这里做后端探测:

  - 两边都是 postgres 且同一 conninfo (同库) -> 走 PostgresDerivedStore.join_state,
    一次 JOIN,status 过滤/排序/截断全下推 SQL。
  - 否则 (json 回退 / 两个独立 PG 实例) -> 保留"两次读 + 内存按 name 合并"老路径,
    JOIN 在跨后端下不成立。

无论走哪条,返回同一形态 list[FactorRow],上层 (list.py / health) 不感知后端差异。
下推纯为预筛:上层仍全量 filter/sort/limit 兜底,故结果两条路径逐位等价。
"""
from dataclasses import dataclass

from ops.core.state import FactorStatus
from ops.infra.config import Config
from ops.infra.store import default_store
from ops.infra.derived import default_derived_store, DerivedRecord


@dataclass
class FactorRow:
    """一个因子的派生数据 + 其生命周期状态摘要 (JOIN 的一行)。

    state 侧只带 list/health 实际要用的两个字段 (status 决定行样式 / fail_stage 列),
    不是整条 FactorRecord —— JOIN 只 SELECT 这两列,省得把 check_history 等大字段拉回。
    无 state 行 (staging-only / 未 backfill) 时 status/last_fail_stage 为 None,
    对齐旧路径 state_records.get(name) 的缺失语义。"""
    derived: DerivedRecord
    status: FactorStatus | None = None
    last_fail_stage: str | None = None


def _both_postgres_same_db(config: Config) -> bool:
    """state 与 derived 是否同一个 PG 库 —— JOIN 成立的前提。
    两边 backend 都是 postgres 且 conninfo 完全一致 (同 host/dbname/user)。
    conninfo 由同一个 _build_pg_conninfo 生成,字符串相等即同库。"""
    if (getattr(config, "state_backend", None) or "").lower() != "postgres":
        return False
    if (getattr(config, "derived_backend", None) or "").lower() != "postgres":
        return False
    sc = getattr(config, "state_postgres_conninfo", None)
    dc = getattr(config, "derived_postgres_conninfo", None)
    return bool(sc) and sc == dc


def query_factors(
    config: Config,
    *,
    author: str | None = None,
    field: str | None = None,
    table_glob: str | None = None,
    has_index: bool = False,
    metrics: list[tuple[str, str, float]] | None = None,
    status: str | None = None,
    sort_by: str | None = None,
    n: int | None = None,
) -> list[FactorRow]:
    """联合读因子派生数据 + 状态。参数语义同 DerivedStore.get_all 的下推参数,外加
    status (state 侧过滤,pg 路径下推 SQL / json 路径留调用方兜底)。

    limit (n) 下推 gate 因后端而异,故在此按后端判定 (调用方无需关心):
      - 只有当 SQL 结果集 == 最终结果集才能下推 n (否则 SQL 后的内存过滤会把行数砍到 <n)。
      - field/tables 下推是近似预筛 (tables glob->LIKE、同键第二条件),恒挡住 n 下推。
      - status: pg JOIN 里精确下推 -> 不挡 n;json 路径 status 不下推 -> 挡 n。
    调用方无论如何都会再跑一遍全量 filter/status/sort/[:n],故这里下推与否结果一致。"""
    can_push_limit_common = field is None and table_glob is None

    if _both_postgres_same_db(config):
        # status 在 JOIN 里精确下推,不影响 limit gate。
        limit_pd = n if can_push_limit_common else None
        store = default_derived_store(config)
        rows = store.join_state(
            author=author, field=field, table_glob=table_glob,
            has_index=has_index, metrics=metrics, status=status,
            sort_by=sort_by, limit=limit_pd,
        )
        return [
            FactorRow(
                derived=rec,
                status=FactorStatus(st) if st is not None else None,
                last_fail_stage=fail,
            )
            for rec, st, fail in rows
        ]

    # 回退: 两次读 + 内存按 name 合并 (json 后端 / 跨库 PG)。
    # status 不下推 get_all (它无 status 参数) -> 挡住 limit 下推。
    limit_pd = n if (can_push_limit_common and status is None) else None
    derived = default_derived_store(config).get_all(
        author=author, field=field, table_glob=table_glob,
        has_index=has_index, metrics=metrics, sort_by=sort_by, limit=limit_pd,
    )
    state = {r.name: r for r in default_store(config).list()}
    out: list[FactorRow] = []
    for name, rec in derived.items():
        s = state.get(name)
        out.append(FactorRow(
            derived=rec,
            status=s.status if s else None,
            last_fail_stage=s.last_fail_stage if s else None,
        ))
    return out
