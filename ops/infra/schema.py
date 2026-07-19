"""三表 schema 引导 —— DDL 的唯一代码入口(已滚出 store __init__)。

原先三个 PG store 各自在 `__init__` 里 ensure_schema:构造带副作用,且 FK 依赖
序靠"恰好 info 先被构造"维持(空库上先建 factor_state 会因内联 FK 引用
factor_info 直接 UndefinedTable)。现在 store 构造零副作用,建表只有两条路:

- 生产:scripts/postgres 的迁移脚本(schema 的真正 owner);
- dev/test/首次接触:本函数按 FK 依赖序(info → state → snapshot)引导,
  由 FactorRepository 在首次触达 PG 时调用、或测试 fixture 显式调用。
  经 pg.ensure_schema 的 (pool, ddl) 去重,每进程只真正执行一次。
"""
from ops.infra.groups.pg_store import _SCHEMA as _GROUP_SCHEMA
from ops.infra.info.pg_store import _SCHEMA as _INFO_SCHEMA
from ops.infra.pg import ensure_schema, get_pool
from ops.infra.snapshot.pg_store import _SCHEMA as _SNAPSHOT_SCHEMA
from ops.infra.store.pg_store import _SCHEMA as _STATE_SCHEMA


def ensure_schemas(conninfo: str) -> None:
    """幂等引导(FK 依赖序:factor_info 是根;produce_group 两表独立,殿后)。"""
    pool = get_pool(conninfo)
    for ddl in (_INFO_SCHEMA, _STATE_SCHEMA, _SNAPSHOT_SCHEMA, _GROUP_SCHEMA):
        ensure_schema(pool, ddl)
