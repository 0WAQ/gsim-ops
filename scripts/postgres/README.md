# ops Postgres (派生层存储)

server-160 上用 Docker 跑的 Postgres，承载因子库**派生层**数据（index / metrics /
datasources / bcorr），替代原来 per-machine 的 `~/.cache/ops/lib/<lib>/*.json`，
让三机共享一份、查询不扫盘。背景见 `.claude/plans/` 派生层迁 PG 计划 +
memory `project_factor_library_storage_architecture`。

## 快速上手

```bash
cd scripts/postgres
# .env 已含 OPS_PG_PASSWORD (gitignore, 不进版本库)
docker compose up -d
docker compose ps                      # 看 healthy
docker exec -it ops-pg psql -U ops -d ops -c '\dt'   # 确认 factor_derived 建好
```

## 关键约定

- **host 端口 15432**（避开默认 5432），容器内仍是 5432。连接串 `host=server-160 port=15432 dbname=ops user=ops`。
- **数据在 named volume `ops-pg-data`**（本地 ext4，绝不放 JFS/网络 FS）。
- **迁移/备份走 `pg_dump`**（`backup.sh`），逻辑备份跨 PG 版本、跨机安全。不要直接搬 volume 物理目录。
- **schema** 见 `init/01-schema.sql`，仅 volume 为空首次 initdb 时自动执行；ops 代码侧有幂等 `_init_schema()` 兜底。

## 备份

```bash
./backup.sh              # 导出到 ./dumps/ops-<时间>.sql.gz, 保留最近 14 份
# 恢复
gunzip -c dumps/ops-XXXX.sql.gz | docker exec -i ops-pg psql -U ops -d ops
```

建议挂 cron（每日）：`0 2 * * * cd /home/wbai/gsim-ops/scripts/postgres && ./backup.sh`

## 迁到别的机器

1. 源机 `./backup.sh` 得到 `.sql.gz`
2. 目标机 `docker compose up -d` 起空库
3. `gunzip -c xxx.sql.gz | docker exec -i ops-pg psql -U ops -d ops` 灌回

派生数据本身可从 JFS 整库 rebuild（`ops list --refresh`），PG 丢了不致命，
备份只是加速用的第二层保险。
