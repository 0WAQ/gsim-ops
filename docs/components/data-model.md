# 数据模型

因子的语义真相(身份 / 状态 / 表现 / 审计)落 Postgres 四张表。本文是当前状态参考;
DDL 正主在 [`../../ops/infra/info/pg_store.py`](../../ops/infra/info/pg_store.py) 等
`pg_store._SCHEMA` + [`../../scripts/postgres/init/01-schema.sql`](../../scripts/postgres/init/01-schema.sql)
(两处由 `tests/test_schema_pin.py` 钉住);演进史见
[`../design/schema-v2.md`](../design/schema-v2.md) + [`../design/schema-v3.md`](../design/schema-v3.md)。

## 四张表

部署:server-160 docker,host 15432,named volume `ops-pg-data`(本地 ext4,绝不放 JFS)。
全部 `id SERIAL` 主键 + `name UNIQUE`,无 `library_id`(永远单库)。

| 表 | 事实族 | 关键列 | 抽象层 |
|---|---|---|---|
| `factor_info` | 身份(不可变) | author / **discovery_method**(NOT NULL, ∈ automated/manual)/ created_at | [`infra/info/`](../../ops/infra/info/) |
| `factor_state` | 生命周期状态机 | status / version / submitted_at / entered_at / updated_at | [`infra/store/`](../../ops/infra/store/) |
| `factor_snapshot` | 测得快照 | ret/shrp/mdd/tvr/fitness / fields·tables(TEXT[])/ delay / max_bcorr / **snapshot_at** | [`infra/snapshot/`](../../ops/infra/snapshot/) |
| `factor_history` | 全操作审计事件 | op / at / actor / passed / failed_stage / fail_reason | [`infra/store/`](../../ops/infra/store/)(同模块) |

**外键 / 级联**:

```
factor_info (根)
  ├── factor_state.name    REFERENCES factor_info(name) ON DELETE CASCADE
  └── factor_snapshot.name REFERENCES factor_info(name) ON DELETE CASCADE
factor_history            —— 刻意无 FK(审计要活过 ops rm)
```

`ops rm` 删 `factor_info` 一行,级联带走 state + snapshot;`factor_history` 事件留存
(指向已删因子的事件是预期,同名重提续写同一 name 的时间线)。

### 各表要点

- **factor_info**:身份是不可变属性。`discovery_method` 自 2026-07-13 起 **NOT NULL +
  CHECK IN ('automated','manual')**(legacy 清理批,'backfill'/NULL 退役)。
- **factor_state**:纯状态机。`status ∈ submitted/checking/active/rejected`(与
  `FactorStatus` 枚举一一对应,DB 是权威);`chk_active_entered` 约束保证 ACTIVE 必有
  `entered_at`。v2b 起不含 `rejected_at`/`last_fail_*`/`check_history`——迁事件表。
- **factor_snapshot**:**测得快照**(schema v3)= 最近一次 check 测得的表现,`snapshot_at` =
  测得时刻。pass 与 correlation/compliance 失败都写(被拒因子也有指标);每行不可变,新测量
  原子替换(delete+insert);永无离线重算(`ops refresh` 已删)。fields/tables 是 TEXT[]
  (GIN 反查)。
- **factor_history**(v2b 全操作审计表):一次操作一条,`op ∈ submit/overwrite/check/approve/
  restage/cancel/rm/backfill/entered`(`HISTORY_OPS` 与 DB `chk_op` 同提交改;'backfill' 是
  历史枚举,命令已退役)。`actor` 经 [`ops/utils/actor.py`](../../ops/utils/actor.py)(SUDO_USER
  优先)。发射与业务写**同事务**——漏记结构上不可能。置 ACTIVE 自动发 'entered' 事件。

## Factor 聚合

[`ops/core/factor.py`](../../ops/core/factor.py) 的 `Factor` 是**全库唯一叫"因子"的类型**,
四切面:

```
Factor
├── identity : FactorIdentity   身份(factor_info)
├── state    : FactorRecord?    状态(factor_state;None = 异常孤儿)
├── snapshot : FactorSnapshot?  测得快照(None = 从未测得)
└── last_fail: HistoryEvent?    最近失败(派生自 factor_history)
```

`correlation_rejected()` 谓词(approve 资格)= REJECTED 且 last_fail 在 correlation stage,
需要 state + history 两个切面,故住在聚合层。

## FactorRepository —— 唯一门面

service 层读写因子只经 [`ops/infra/repository.py`](../../ops/infra/repository.py) 的
`FactorRepository`(构造便宜,store 懒加载):

| 面 | 方法 |
|---|---|
| 读 | `get` / `find`(单条三表 LEFT JOIN,"库内因子集"定义处)/ `record` / `exists` / `history` / `latest_check_ats` |
| 写 | `register`(info+state 单事务原子写)/ `transition`(CAS)/ `append_check` / `attach_snapshot` / `discard_snapshot` / `delete`(info 级联)/ `lock` |
| 产物 | `paths` / `archive` / `recall` / `unstage` / `purge_artifacts`(ArtifactScope) |

`find` 是 list 的联合读入口,也是"库内因子集 = `status != 'submitted'`"的定义处(2026-07-07
Wave 2 起纯 PG 零扫盘)。深度见 [`../../ops/infra/CLAUDE.md`](../../ops/infra/CLAUDE.md) Repository 节。

## SSOT 表

每个事实族只有一个正主,其余是投影或缓存——改代码第一问"你在问正主吗?"。完整表在
[`../../CLAUDE.md`](../../CLAUDE.md) "SSOT 表",要点:

| 事实族 | 正主 |
|---|---|
| 因子集(什么算在库) | PG `factor_state.status != 'submitted'` |
| 身份 | PG `factor_info` |
| 测得表现 | PG `factor_snapshot` |
| stage 身份 / 顺序 / 路由 | `services/check/stages.py` 的 `PIPELINE` |
| 盘面布局 | `ops/core/paths.py::FactorPaths`([storage-layout.md](storage-layout.md)) |
| 操作事件 | PG `factor_history` |

→ 回 [架构总览](../architecture.md#6-数据模型)
