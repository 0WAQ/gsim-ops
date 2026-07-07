"""跨 info + state + snapshot 的联合读 (list / health 热路径).

2026-07-06 重构: 从 derived+state 两表改为 info+state+snapshot 三表。

- factor_info: 身份信息 (author/discovery_method)
- factor_state: 生命周期状态
- factor_snapshot: 入库时快照 (metrics/datasources/bcorr/index)

三表通过 name 关联，LEFT JOIN 保证即使缺 state/snapshot 也能返回基础信息。
"""
from dataclasses import dataclass

from ops.core.state import FactorStatus
from ops.infra.config import Config
from ops.infra.info import FactorInfo, default_info_store
from ops.infra.store import default_store
from ops.infra.snapshot import FactorSnapshot, default_snapshot_store


@dataclass
class FactorRow:
    """一个因子的完整信息 (info + state + snapshot 三表 JOIN 的一行)。

    info: 身份信息（永远有）
    state: 生命周期状态（可能为 None，staging-only / 未入库）
    snapshot: 入库时快照（可能为 None，未通过 check）
    """
    info: FactorInfo
    status: FactorStatus | None = None
    last_fail_stage: str | None = None
    snapshot: FactorSnapshot | None = None


def _both_postgres_same_db(config: Config) -> bool:
    """state 与 snapshot 是否同一个 PG 库 —— JOIN 成立的前提。"""
    if (getattr(config, "state_backend", None) or "").lower() != "postgres":
        return False
    # snapshot 永远用 postgres（无 JSON 回退），但为了一致性检查 conninfo
    sc = getattr(config, "state_postgres_conninfo", None)
    return bool(sc)  # 只要有 postgres conninfo 就能 JOIN


def query_factors(
    config: Config,
    *,
    author: str | None = None,
    field: str | None = None,
    table_glob: str | None = None,
    metrics: list[tuple[str, str, float]] | None = None,
    status: str | None = None,
    sort_by: str | None = None,
    n: int | None = None,  # noqa: ARG001 — 语义参数保留,见下方 limit 说明
) -> list[FactorRow]:
    """联合读"库内因子"的完整信息 (info + state + snapshot)。

    **因子集判据 (2026-07-07 Wave 2, JOURNAL V1)**: 库内因子 =
    `factor_state.status != 'submitted'`(SUBMITTED 在 staging 不在库)。
    这是唯一权威 —— 原先由 LibraryScanner 扫盘白名单界定,PG 迁移后仍每次
    付 ~25s 扫盘税(full-review G6/P0-4)。`status` 给定时按其精确过滤
    (包括显式查 submitted)。PG 与磁盘的漂移(崩溃残留等)属对账问题,
    由后续 ops doctor 处理,不再让每次 list 付对账税。

    **limit (n) 不再下推**(P0-5 修复): 旧实现把 LIMIT 塞进 snapshot 单表
    查询且无稳定 ORDER BY,PG 返回任意 N 行,再与 info/state 交集 → 行数
    错乱、入选因子 metrics 空白。三表内存合并模型下 limit 只能在合并后截断,
    由调用方 (list.py 的 [:n]) 执行;参数保留是为未来单条 SQL JOIN 时恢复
    下推(TODO)。
    """
    # 当前只实现 Postgres 路径（单库永远用 PG）
    if not _both_postgres_same_db(config):
        raise NotImplementedError("query_factors 当前只支持 Postgres 后端")

    snapshot_store = default_snapshot_store(config)

    # TODO: 单条 SQL 三表 LEFT JOIN(届时 limit/status 一并下推)
    # 临时方案：三次读 + 内存合并
    info_store = default_info_store(config)
    state_store = default_store(config)

    # 1. 读 state —— 因子集的定义者
    if status:
        states = {r.name: r for r in state_store.list(status=FactorStatus(status))}
    else:
        states = {r.name: r for r in state_store.list()
                  if r.status != FactorStatus.SUBMITTED}

    # 2. 读 info（可按 author 过滤;身份行是三表的根,正常情况必存在）
    infos = info_store.list(author=author)

    # 3. 读 snapshot（field/tables/metrics/sort 下推;limit 不下推,见 docstring）
    snapshots = snapshot_store.list(
        field=field,
        table_glob=table_glob,
        metrics=metrics,
        sort_by=sort_by,
        limit=None,
    )

    # 4. 内存合并:以 state 因子集为基,info 提供身份(兼做 author 过滤)
    infos_by_name = {i.name: i for i in infos}
    result = []
    for name, state in states.items():
        info = infos_by_name.get(name)
        if info is None:
            # author 过滤未命中,或(异常)孤儿 state —— 两种都不属于本次结果集
            continue
        # snapshot 侧过滤条件命中才保留
        if (field or table_glob or metrics) and name not in snapshots:
            continue
        result.append(FactorRow(
            info=info,
            status=state.status,
            last_fail_stage=state.last_fail_stage,
            snapshot=snapshots.get(name),
        ))

    return result
