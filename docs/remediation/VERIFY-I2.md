# I2 复验手册(160 执行者;分支 claude/i2-test-isolation)

**目标**:在生产同款环境(160,ops_test 在生产 PG 实例上)复验 I2 测试基建:
pg 组 0 skipped、并行两进程互不干扰、ops_test 零残留、e2e 在新隔离下仍绿。

**红线**:
1. 只碰 `ops_test` 库,**不对生产 `ops` 库执行任何语句**(连 SELECT 都不需要);
2. 不动生产盘面(本手册全部是测试,数据路径都在 pytest tmp);
3. 每步贴原文;不符即停。
4. 本手册**不需要窗口**:per-session schema 隔离后,测试与生产 ops 命令、
   与其它机器的测试并行都安全 —— 这正是要验的命题。

## 阶段 1 · 同步 + 全量 fast suite

```bash
cd ~/gsim-ops && git fetch origin claude/i2-test-isolation \
  && git checkout claude/i2-test-isolation && git pull
uv sync --group dev
uv run pytest -m "not slow" -q          # 预期:134 passed,0 skipped,0 failed
uv run pytest -m pg -q                  # 预期:57 passed,0 skipped
```

## 阶段 2 · 并行不撞(I2 核心命题)

```bash
uv run pytest -m pg -q > /tmp/i2-par1.log 2>&1 &
uv run pytest -m pg -q > /tmp/i2-par2.log 2>&1 &
wait
tail -1 /tmp/i2-par1.log                # 预期:57 passed
tail -1 /tmp/i2-par2.log                # 预期:57 passed
```

## 阶段 3 · ops_test 零残留

```bash
uv run python - <<'PY'
import psycopg
pw = next(l.split('=',1)[1].strip() for l in open('scripts/postgres/.env')
          if l.startswith('OPS_PG_PASSWORD='))
c = psycopg.connect(f'host=10.9.100.160 port=15432 dbname=ops_test user=ops password={pw}')
print("t_* schemas:", c.execute("SELECT nspname FROM pg_namespace WHERE nspname LIKE 't_%'").fetchall())
print("残留表:", c.execute("SELECT schemaname, tablename FROM pg_tables WHERE schemaname NOT IN ('pg_catalog','information_schema')").fetchall())
c.close()
PY
# 预期:t_* schemas 为 [](并行两轮跑完全自清)。
# 残留表:I2 前的测试建过 public.factor_* 三表(可能带旧测试行)——属预期的
# 历史残留,贴出来即可,不删;判读后决定是否清(I2 后测试永不再碰 public)。
```

## 阶段 4 · e2e(新隔离下的真 pipeline)

```bash
uv run pytest -m e2e -v 2>&1 | tail -5   # 预期:6 passed(~7min)
```

## 阶段 5 · 报告

写 `VERIFY-I2-RESULT.md` push 回本分支:阶段 1-2 的 pytest 汇总行原文、
阶段 3 两行输出原文(含历史残留如有)、阶段 4 汇总行。全绿后判读方提 PR。
任何一步不符:停在那一步,贴原文。
