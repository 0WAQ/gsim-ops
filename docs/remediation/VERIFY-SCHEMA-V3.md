# schema v3 执行手册(执行者;分支 claude/schema-v3,零 DDL,免禁写窗口)

**目标**:测得快照回填(738 correlation 被拒)+ created_at 修正(81 违反者)。
验收标准 = `ops list -s rejected` 出指标 + doctor 全绿。

**红线**:每阶段贴原文等判读;dry-run 先行,--apply 等放行;
脚本零 DDL 纯 INSERT/UPDATE,幂等(二跑零命中);任何 ERROR 停贴原文。

## 阶段 1 · 同步 + 门禁(160)

```bash
cd ~/gsim-ops && git fetch origin claude/schema-v3 \
  && git checkout claude/schema-v3 && git pull && uv sync --group dev
uv run pytest -m "not slow" -q     # 预期 174 passed, 0 skipped
```

## 阶段 2 · 备份 + dry-run(160)

```bash
docker exec ops-pg pg_dump -U ops -d ops -t factor_info -t factor_snapshot \
  -t factor_history > /tmp/backup_v3_$(date +%Y%m%d%H%M).sql
ls -lh /tmp/backup_v3_*.sql && grep -c "dump complete" /tmp/backup_v3_*.sql

cd scripts/postgres
python3 migrate_v3_measured_snapshots.py \
  --conninfo "host=127.0.0.1 port=15432 dbname=ops user=ops password=$(grep OPS_PG_PASSWORD .env | cut -d= -f2)" \
  --alpha-src /tank/vault/alphalib/alpha_src
```

贴:备份大小 + dry-run 全文。**判读锚点**:[A] 违反行 ≈ 81;
[B] 候选 ≈ 738、可回填应接近候选(skip 名单全部贴出单独判读)。

## 阶段 3 · --apply(判读放行后)

同命令加 `--apply`,贴全文(A 两个修正行数 + B 回填条数)。

## 阶段 4 · 复验(160,分支代码)

```bash
uv run ops list -u zxu -s rejected 2>/dev/null | head -12   # 指标/delay 应出现
uv run ops list 2>/dev/null | tail -1                        # Total 8252 不变
uv run ops status AlphaZxu_260703_SmartMoneyDiv_delay1       # 时间线正常
uv run ops doctor ; echo "exit=$?"   # 预期仅 pool-ghost 合法 WARN,exit=0
# created_at 修正复验:违反行归零
docker exec ops-pg psql -U ops -d ops -c "SELECT count(*) FROM factor_info i
  JOIN factor_state s USING (name)
  WHERE s.submitted_at IS NOT NULL AND i.created_at > s.submitted_at;"
```

五段贴回。判读通过后我方提 PR;合并后四机 `git pull` +
`uv tool install --reinstall .`(memory 那条),台账 ⬜ → ✅。
