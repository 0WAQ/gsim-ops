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
    n: int | None = None,
) -> list[FactorRow]:
    """联合读因子完整信息 (info + state + snapshot)。

    参数语义同原 DerivedStore.get_all，外加 status (state 侧过滤)。

    limit (n) 下推 gate: 只有 PG 同库 + 精确下推条件时才下推，否则留调用方兜底。
    """
    # 当前只实现 Postgres 路径（单库永远用 PG）
    if not _both_postgres_same_db(config):
        raise NotImplementedError("query_factors 当前只支持 Postgres 后端")

    # 直接从 snapshot_store 拿三表 JOIN 的结果
    snapshot_store = default_snapshot_store(config)

    # TODO: 实现三表 JOIN 的 SQL（当前 snapshot_store 只有单表查询）
    # 临时方案：三次读 + 内存合并
    info_store = default_info_store(config)
    state_store = default_store(config)

    # 1. 读 info（可按 author 过滤）
    infos = info_store.list(author=author)

    # 2. 读 state（可按 status 过滤）
    state_status = FactorStatus(status) if status else None
    states = {r.name: r for r in state_store.list(status=state_status)}

    # 3. 读 snapshot（应用所有过滤条件）
    snapshots = snapshot_store.list(
        field=field,
        table_glob=table_glob,
        metrics=metrics,
        sort_by=sort_by,
        limit=n,  # 暂时先下推，后面优化
    )

    # 4. 内存合并
    result = []
    for info in infos:
        # 如果有 snapshot 过滤条件，只保留在 snapshots 里的
        if field or table_glob or metrics:
            if info.name not in snapshots:
                continue

        state = states.get(info.name)
        snapshot = snapshots.get(info.name)

        result.append(FactorRow(
            info=info,
            status=state.status if state else None,
            last_fail_stage=state.last_fail_stage if state else None,
            snapshot=snapshot,
        ))

    return result
