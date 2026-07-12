# ops doctor v1 复验手册(执行者;分支 claude/ops-doctor-v1)

**目标**:生产库首轮对账基线 + 三类修复实战。这一轮的产出有双重身份:
验收 doctor 本身,同时产出 **v1.1 放闸判读材料**(pnl/feature 孤儿名单)。

**红线**:
1. 每一步先跑只读(`ops doctor` 不带 --fix,零 sudo 零写),贴原文;
2. `--fix` 只按本手册点名的族执行;确认提示里的 FixPlan 三句话(动作/删什么/
   不碰)与待删清单**先贴报告再回车**;任何清单看着不对:停,不要 y;
3. 不碰生产 ops 库的 SQL(migrate_snapshot_at.py 的 dry-run 输出先贴,
   --apply 等判读方回复后再执行);
4. 与在跑 check 并发是安全的(设计如此),但 missing/checking 类瞬态漂移
   属正常 —— 报告里看到不要慌,复跑一次对比;
5. 不符即停。

## 阶段 1 · 同步 + 门禁(160)

```bash
cd ~/gsim-ops && git fetch origin claude/ops-doctor-v1 \
  && git checkout claude/ops-doctor-v1 && git pull
uv sync --group dev
uv run pytest -m "not slow" -q        # 预期 155 passed, 0 skipped
```

## 阶段 2 · 只读基线(160,非 root 裸跑)

```bash
uv run ops doctor ; echo "exit=$?"           # 预期:秒级出报告、全程无 sudo 提示
uv run ops doctor --format json > /tmp/doctor-160.json 2>/dev/null
python3 -c "import json; d=json.load(open('/tmp/doctor-160.json')); \
print({f['family_id']: len(f['findings']) for f in d['families']})"
```

贴:汇总表原文 + 各族计数。**基线判读锚点**(判读方核对,执行者只贴):
- snapshot-stale ≈ 662(illegal + mismatch 两 kind 拆分计数);
- pool-ghost ghost ≈ 0(2026-07-11 刚清过 622;若有新增贴名单);
- info-orphan 对 2026-07-06 迁移账(当时补了 20 个 hwang 孤儿 state);
- artifact-orphan 的 pnl/feature 名单 = v1.1 放闸判读材料,全量在 JSON 里;
- src-drift lib-missing 预期 0(有则是真源码丢失,最高优先级上报)。

## 阶段 3 · 修复(160,判读方看过阶段 2 输出后才执行)

```bash
uv run ops doctor --fix snapshot-stale       # 交互确认:先贴 FixPlan+清单,再 y
uv run ops doctor --family snapshot-stale ; echo "exit=$?"   # 复跑:illegal 应归零
uv run ops list -u wbai 2>&1 | grep -c WARNING || true       # 刷屏应显著下降
```

illegal 清完后跑 mismatch 侧一次性迁移(dry-run 先贴,--apply 等回复):

```bash
uv run ops doctor --format json > /tmp/doctor-160-2.json 2>/dev/null
uv run python scripts/postgres/migrate_snapshot_at.py --input /tmp/doctor-160-2.json
# ……贴 dry-run 输出,判读方回复后:
uv run python scripts/postgres/migrate_snapshot_at.py --input /tmp/doctor-160-2.json --apply
uv run ops doctor --family snapshot-stale ; echo "exit=$?"   # 预期两 kind 全零
uv run ops list 2>/dev/null | tail -1                        # Total 不变 + 无 WARNING
```

## 阶段 4 · 170 本机面(dump-orphan 是 host scope,只能在 170 看)

```bash
# 170(cd ~/gsim-ops 同步本分支 + uv tool 重装后)
~/.local/bin/ops doctor --family dump-orphan       # 只读;预期为空或列出跨机 rm 残留
# 如有残留:贴名单 → --fix dump-orphan(交互确认)→ 复跑归零
# 回 160:uv run ops setup --check 预期 FAIL 0(共享面未被 170 的 fix 动过)
```

## 阶段 5 · 报告

`VERIFY-DOCTOR-RESULT.md` push 回本分支:阶段 1 pytest 行、阶段 2 汇总表 +
计数 dict、阶段 3 每次确认提示原文 + 复跑汇总行 + migrate dry-run/apply 原文 +
list Total、阶段 4 的 170 输出。/tmp/doctor-160.json 留在 160(v1.1 判读材料),
路径写进报告即可不贴全文。任何一步不符:停在那一步。
