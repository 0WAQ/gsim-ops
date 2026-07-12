# schema v2a 执行手册(执行者;分支 claude/schema-v2)

**目标**:生产 PG(160 docker `ops-pg`)执行两份迁移:
1. `migrate_drop_snapshot_index_cols.sql` —— **补执行**。has_pnl/dump_days
   代码侧 2026-07-06 已删,但删列从未在生产跑过(用户查活表发现),两列
   现在是僵尸列;
2. `migrate_v2a_state_check.sql` —— factor_state 加 `chk_active_entered`
   约束(status='active' ⇒ entered_at 非空),把"不该 NULL 的状态下 NULL"
   挡在写入口。

**红线**:
1. 只跑本手册点名的两份 SQL,不做任何其它生产库写操作;
2. 备份先行(阶段 2),备份没落盘不进阶段 4;
3. 阶段 3 的 `violating_rows` **非 0 即停**,贴名单等判读,不要继续
   (脚本事务内 ADD CONSTRAINT 会自然失败回滚,但按流程就不该走到那);
4. 每阶段贴原文等判读方回复,不符即停;
5. 选无 check 在跑的空档执行(两条 ALTER 都是秒级,但拿表级排他锁,
   避免与消费中的写事务互相排队)。

## 阶段 1 · 同步 + 门禁(160)

```bash
cd ~/gsim-ops && git fetch origin claude/schema-v2 \
  && git checkout claude/schema-v2 && git pull
uv sync --group dev
uv run pytest -m "not slow" -q        # 预期 162 passed, 0 skipped
```

贴:pytest 末行。

## 阶段 2 · 备份(160)

```bash
docker exec ops-pg pg_dump -U ops -d ops -t factor_snapshot -t factor_state \
  > /tmp/backup_v2a_$(date +%Y%m%d%H%M).sql
ls -lh /tmp/backup_v2a_*.sql && tail -3 /tmp/backup_v2a_*.sql
```

贴:文件大小 + tail(确认 dump 完整收尾,末行应是 `-- PostgreSQL database dump complete` 附近)。

## 阶段 3 · 只读前置核对(160)

```bash
# 3a. 僵尸列现状:预期 has_pnl / dump_days 两列**还在**(这正是要补的账)
docker exec ops-pg psql -U ops -d ops -c '\d factor_snapshot'

# 3b. 约束前置:预期 violating_rows = 0
docker exec ops-pg psql -U ops -d ops -c \
  "SELECT count(*) AS violating_rows FROM factor_state
   WHERE status = 'active' AND entered_at IS NULL;"
```

贴:两段原文。**3b 非 0:停**,补贴名单等判读:
`SELECT name, status, entered_at, updated_at FROM factor_state WHERE status='active' AND entered_at IS NULL;`

## 阶段 4 · 执行迁移(160;判读方放行后)

```bash
cd ~/gsim-ops/scripts/postgres
docker exec -i ops-pg psql -U ops -d ops -v ON_ERROR_STOP=1 \
  < migrate_drop_snapshot_index_cols.sql
docker exec -i ops-pg psql -U ops -d ops -v ON_ERROR_STOP=1 \
  < migrate_v2a_state_check.sql
```

贴:两段完整输出(第二段应含 `violating_rows` 一行 = 0 + 两条 ALTER TABLE +
COMMIT)。任一段报错:停,贴原文,**不要重试**(两脚本均幂等,但重试也要
等判读)。

## 阶段 5 · 复验(160)

```bash
docker exec ops-pg psql -U ops -d ops -c '\d factor_snapshot'   # 两列消失
docker exec ops-pg psql -U ops -d ops -c '\d factor_state'      # Check constraints 出现 chk_active_entered
uv run ops doctor ; echo "exit=$?"    # 预期与 v1.1 收官基线一致:仅 pool-ghost 合法 WARN,exit 0
uv run ops list 2>/dev/null | tail -3 # Total 8252 不变
```

贴:四段原文。约束的拦截行为不在生产验证(不写垃圾行),由分支测试盖
(`tests/test_state_store_pg.py` + CI postgres service)。

## 收尾

全绿后判读方更新 `scripts/postgres/README.md` 迁移台账(两行 ⬜ → ✅)并推分支;
执行者无需改文档。
