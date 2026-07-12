# schema v2b 执行手册(执行者;分支 claude/schema-v2b,短窗口)

**目标**:生产 PG 执行 `migrate_v2b_history.sql`(factor_history 建表 +
check_history 展开回填 + factor_state 删四列 + fields/tables TEXT[]),
随后四机滚存新代码。

**红线**:
1. **全库禁写窗口**:从阶段 3 开始到阶段 6 结束,四机(160/150/170/144)
   不跑任何 ops 命令(读也不跑 —— 旧代码 SELECT 已删除的列会报错)。
   开窗前确认:170 无 check 在跑(`ops status --status checking` 为空)、
   无 cron 消费(尚未 cron 化,确认无人手跑即可);
2. **顺序不可换**:迁移在前、代码滚存在后。新代码先上会往 factor_history
   写事件,迁移的行数核对会响亮失败(设计如此,防静默双记);
3. 备份没落盘不进阶段 3;脚本**非幂等只跑一次**,任何 ERROR:停,贴原文,
   不要重试(单事务已自动回滚,数据原样);
4. 每阶段贴原文等判读,不符即停。

## 阶段 1 · 同步 + 门禁(160)

```bash
cd ~/gsim-ops && git fetch origin claude/schema-v2b \
  && git checkout claude/schema-v2b && git pull
uv sync --group dev
uv run pytest -m "not slow" -q        # 预期 173 passed, 0 skipped
```

## 阶段 2 · 备份 + 只读前置核对(160)

```bash
docker exec ops-pg pg_dump -U ops -d ops \
  -t factor_info -t factor_state -t factor_snapshot \
  > /tmp/backup_v2b_$(date +%Y%m%d%H%M).sql
ls -lh /tmp/backup_v2b_*.sql && grep -c "dump complete" /tmp/backup_v2b_*.sql

# 基线三数(迁移后核对用):JSONB 元素总数 / info 行数 / entered_at 非空数
docker exec ops-pg psql -U ops -d ops -c \
  "SELECT (SELECT coalesce(sum(jsonb_array_length(check_history)),0) FROM factor_state) AS checks,
          (SELECT count(*) FROM factor_info) AS infos,
          (SELECT count(*) FROM factor_state WHERE entered_at IS NOT NULL) AS entered;"

# 脏元素预检(预期 0;非 0 停,贴名单等判读)
docker exec ops-pg psql -U ops -d ops -c \
  "SELECT count(*) AS bad FROM factor_state s, jsonb_array_elements(s.check_history) c
   WHERE (c->>'passed')::boolean IS FALSE AND c->>'failed_stage' IS NULL;"
```

贴:文件大小 + 三数 + bad。**bad 非 0:停**。

## 阶段 3 · 执行迁移(160;判读方放行后,窗口开启)

```bash
cd ~/gsim-ops/scripts/postgres
docker exec -i ops-pg psql -U ops -d ops -v ON_ERROR_STOP=1 < migrate_v2b_history.sql
```

贴:完整输出(应含 `NOTICE: check 事件展开: N 条`,N == 阶段 2 的 checks;
以 COMMIT 收尾)。

## 阶段 4 · DB 形状复验(160)

```bash
docker exec ops-pg psql -U ops -d ops -c '\d factor_state'     # 6 业务列,无 check_history
docker exec ops-pg psql -U ops -d ops -c '\d factor_history'   # 新表 + ix_fh_name_at
docker exec ops-pg psql -U ops -d ops -c '\d factor_snapshot'  # fields/tables 为 text[]
docker exec ops-pg psql -U ops -d ops -c \
  "SELECT op, count(*) FROM factor_history GROUP BY op ORDER BY op;"
```

贴:四段。核对锚点:check == 阶段 2 checks;submit == infos;
entered == entered(合成事件 actor='migration')。

## 阶段 5 · 新代码功能复验(160,仍在分支)

```bash
uv run ops list 2>/dev/null | tail -3      # Total 8252 不变
uv run ops list --filter-by tables=ashare* 2>/dev/null | tail -3   # TEXT[] glob 下推
uv run ops status <一个有 check 历史的因子>  # timeline 渲染:submit/check/entered...
uv run ops doctor ; echo "exit=$?"          # 预期与基线一致(仅 pool-ghost WARN)
uv run pytest -m e2e -q                     # 真管线 e2e,~85s,预期 6 passed
```

贴:五段原文(status 选个熟悉的因子,时间线与迁移前 check_history 对照)。

## 阶段 6 · 四机滚存(用户合 PR 后)

150/144/170 依次 `git pull`(main);170 若走 uv tool 安装则重装;各机
`uv run ops list | tail -1` 冒烟(Total 一致)。全绿后窗口解除,判读方更新
README 台账(v2b 行 ⬜ → ✅)。
