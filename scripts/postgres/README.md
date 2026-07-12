# ops Postgres(因子三表真相源)

server-160 Docker 跑的 Postgres,承载因子库的 **PG 真相源三表**(2026-07-06
三表重构后;原"派生层 factor_derived"已于 2026-07-07 Wave 2 退役删除):

| 表 | 内容 | 代码侧 |
|---|---|---|
| `factor_info` | 身份(author/discovery_method/created_at;三表的根,FK 级联于它) | `ops/infra/info/` |
| `factor_state` | 生命周期状态机(status/version/时间戳;v2b 后无 check_history/last_fail_*) | `ops/infra/store/` |
| `factor_history` | 全操作审计事件(op/at/actor + check 四列;**无 FK,活过 rm**,v2b) | `ops/infra/store/`(同模块) |
| `factor_snapshot` | 入库时不可变快照(metrics/datasources/delay/bcorr,snapshot_at=entered_at) | `ops/infra/snapshot/` |

## 快速上手

```bash
cd scripts/postgres
# .env 已含 OPS_PG_PASSWORD(gitignore,不进版本库;新机器从 160 scp,600 权限)
docker compose up -d
docker compose ps                      # 看 healthy
docker exec -it ops-pg psql -U ops -d ops -c '\dt'   # 预期 factor_info/state/snapshot 三表
```

## 关键约定

- **host 端口 15432**(避开默认 5432),容器内 5432。连接串
  `host=10.9.100.160 port=15432 dbname=ops user=ops`;
- **数据在 named volume `ops-pg-data`**(本地 ext4,绝不放 JFS/网络 FS);
- **schema 双真相源**:`init/01-schema.sql`(volume 为空首次 initdb 自动执行)
  是代码侧三个 `pg_store._SCHEMA` 的镜像,改表结构两处同改 ——
  一致性由 `tests/test_schema_pin.py` 钉住(drift 即红);代码侧幂等引导是
  `ops/infra/schema.py::ensure_schemas`;
- **测试库 `ops_test`** 同实例(per-session schema 隔离,见 tests/README.md),
  与生产 `ops` 库隔离;
- **迁移/备份走 `pg_dump`**(`backup.sh`,保留最近 14 份),不搬 volume 物理目录。

## 迁移脚本台账(按时间序;生产执行前先 pg_dump 备份)

| 脚本 | 作用 | 生产执行状态 |
|---|---|---|
| `migrate_to_snapshot.sql` | 双表 → 三表重构 | ✅ 2026-07-06 |
| `backfill_discovery_method.py` | discovery_method 回填 | ✅ 2026-07-06 |
| `migrate_drop_derived.sql` | 删 derived 僵尸表 | ✅ 2026-07-08(三机滚存窗口) |
| `migrate_drop_snapshot_index_cols.sql` | 删 has_pnl/dump_days 僵尸列 | ✅ 2026-07-12(v2a 补执行;用户查活表发现从未跑) |
| `migrate_snapshot_at.py` | mismatch 时间戳一次性修正(doctor JSON 名单) | ✅ 2026-07-12(doctor v1 收官,UPDATE 20 行) |
| `migrate_v2a_state_check.sql` | chk_active_entered 约束 | ✅ 2026-07-12(v2a) |
| `migrate_v2b_history.sql` | factor_history 建表 + check_history 展开回填 + state 删四列 + fields/tables TEXT[] | ✅ 2026-07-12(v2b;22937 事件,锚点 6988/8419/7530 全中) |

## 备份

```bash
./backup.sh              # 导出到 ./dumps/ops-<时间>.sql.gz
gunzip -c dumps/ops-XXXX.sql.gz | docker exec -i ops-pg psql -U ops -d ops   # 恢复
```
