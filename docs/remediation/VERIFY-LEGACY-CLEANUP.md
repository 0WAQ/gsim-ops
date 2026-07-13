# legacy 清理批执行手册(执行者;分支 claude/legacy-cleanup)

**目标**:三个迁移脚本落生产 —— ①472 条 snapshot_at 漂移拉正;②compliance
被拒 22 条补跑测得快照;③discovery_method 129 条 NULL 归一 + `chk_discovery`
收窄 + `SET NOT NULL` 收口。附带 ACTIVE 池补账(31 条 bcorr 池盲区)。
验收 = doctor snapshot-stale mismatch 0 + timeline-drift 0 + discovery
收口 ✅ + Total 8252 不变。

**红线**:每阶段贴原文等判读;dry-run 先行,--apply 等放行;备份先行;
任何 ERROR / 计数与锚点不符 → 停,贴原文。锚点统计范围:2026-07-13 全库。

## 阶段 1 · 同步 + 门禁(160)

```bash
cd ~/gsim-ops && git fetch origin claude/legacy-cleanup \
  && git checkout claude/legacy-cleanup && git pull && uv sync --group dev
uv run pytest -m "not slow" -q     # 预期 174 passed, 0 skipped
uv run python -m ops.main backfill --help 2>&1 | head -3   # 预期: invalid choice(已退役)
```

**⚠ 本手册所有 ops CLI 验证一律 `uv run python -m ops.main <cmd>`,不用
`uv run ops`**:项目 not packaged,`uv run ops` 会 fall through 到 PATH 上
全局 uv-tool 旧 shim(2026-07-13 执行者实测抓获)—— 测的是已部署版本不是
分支代码。**不要**中途 `uv tool install --reinstall` 把未合并分支装成全局
命令 —— 生产工具跟 main 走,四机滚存在 PR 合并后照常做。

## 阶段 2 · 生成 --assign 名单(只读;dm-probe 产物还在 /tmp)

拍板映射(2026-07-13,用户):**ybai / zxu / cchang / sli → manual;
hwang → automated;fguo(2 条 active)待点名**。

```bash
# 名单严格从 121 条 unresolved(/tmp/dm-unresolved.txt)生成,fguo 除外 ——
# 8 条池位置可判的不进名单(留给脚本按池判,遵守"池位置优先"拍板)
docker exec ops-pg psql -U ops -d ops -At -F' ' -c "
SELECT i.name, CASE WHEN i.author IN ('ybai','zxu','cchang','sli') THEN 'manual'
                    WHEN i.author = 'hwang' THEN 'automated' END
FROM factor_info i
WHERE i.author <> 'fguo'
  AND i.name = ANY(string_to_array('$(paste -sd, /tmp/dm-unresolved.txt)', ','))" \
  > /tmp/dm-assign.txt
wc -l /tmp/dm-assign.txt          # 预期 119(121 - fguo 2)
grep -cv ' manual$\| automated$' /tmp/dm-assign.txt   # 预期 0(无缺映射行)
# fguo 待点名的 2 条:贴出来等用户拍板
docker exec ops-pg psql -U ops -d ops -c "
SELECT i.name, s.status, s.entered_at FROM factor_info i
JOIN factor_state s USING (name)
WHERE i.discovery_method IS NULL AND i.author = 'fguo';"
```

贴:两个计数 + fguo 2 条全行。**等我方回 fguo 的 2 行**
(`<name> automated|manual`),追加进 /tmp/dm-assign.txt 后进阶段 3。

顺手排查(发现①)**已完成,假设不成立**(2026-07-13 执行者实测):129/129
NULL-dm 的 submit 事件 actor=migration —— 全是 v2b 迁移合成的存量档案,
不是旧部署机器的真实提交;不存在正在产 NULL 的部署漂移,无 rev 可追。

## 阶段 3 · 备份(160)

```bash
docker exec ops-pg pg_dump -U ops -d ops -t factor_info -t factor_state \
  -t factor_snapshot -t factor_history \
  > /tmp/backup_legacy_$(date +%Y%m%d%H%M).sql
ls -lh /tmp/backup_legacy_*.sql && grep -c "dump complete" /tmp/backup_legacy_*.sql
```

## 阶段 4 · 三脚本 dry-run(等判读,勿 --apply)

```bash
cd ~/gsim-ops
# ① snapshot_at 漂移:纯 SQL,事务自带守卫;先跑只读预览(手册头部注释里的
#    SELECT count(*),预期 ≈472),贴计数 —— SQL 本身等阶段 5 放行再执行
docker exec ops-pg psql -U ops -d ops -At -c "
SELECT count(*) FROM factor_snapshot n JOIN factor_state st USING (name)
LEFT JOIN (SELECT DISTINCT ON (name) name, at FROM factor_history
           WHERE op='check' ORDER BY name, at DESC, id DESC) lc USING (name)
WHERE coalesce(lc.at, st.entered_at) IS NOT NULL
  AND n.snapshot_at IS DISTINCT FROM coalesce(lc.at, st.entered_at);"
# ② compliance 22 补跑(dry-run 会真跑 simsummary,只算不写)
uv run python scripts/postgres/backfill_compliance_snapshots.py
# ③ discovery 归一(dry-run;fguo 2 行未追加前 unresolved=2 属预期)
uv run python scripts/postgres/migrate_discovery_notnull.py --assign /tmp/dm-assign.txt
```

贴:三段全文。**判读锚点**:①≈472;②候选 22 / 可补跑接近 22(skip 名单
全贴);③候选 129,可判定 = 8(pool)+ 119(assign)= 127,unresolved =
fguo 2 条(点名追加后复跑 dry-run 应 unresolved 0),冲突名单全贴。

## 阶段 5 · --apply(判读放行后,顺序执行,逐个贴全文)

```bash
docker exec -i ops-pg psql -U ops -d ops -v ON_ERROR_STOP=1 \
  < scripts/postgres/migrate_legacy_snapshot_at.sql        # ①(事务内自验)
uv run python scripts/postgres/backfill_compliance_snapshots.py --apply   # ②
uv run python scripts/postgres/migrate_discovery_notnull.py \
  --assign /tmp/dm-assign.txt --apply                      # ③(应打印 收口完成 ✅)
```

## 阶段 6 · ACTIVE 池补账(31 条 bcorr 池盲区,发现②)

赋值前两池皆无的 ACTIVE 因子,pnl 从未进对比池(当年 dm 未知分流被跳过)。
**只补本批 31 条**(dm-probe 里 active 且 N/N 的名单)—— 全库 ACTIVE 里
其它 missing 是 approve 豁免的合法形态,只报不动,**绝不能全量补**:

```bash
awk '$3=="active" && $4=="auto=N" && $5=="man=N" {print $1}' /tmp/dm-probe.txt \
| while read n; do
    dm=$(docker exec ops-pg psql -U ops -d ops -At -c \
         "SELECT discovery_method FROM factor_info WHERE name='$n'")
    pool=/tank/vault/alphalib/pnl_${dm}
    [ -f /tank/vault/alphalib/alpha_pnl/$n ] && [ ! -e $pool/$n ] \
      && echo "$n -> $dm" && sudo cp -p /tank/vault/alphalib/alpha_pnl/$n $pool/
  done
```

贴补账清单。**预期 =31 条**(zxu 28 → manual,hwang 1 → automated,
fguo 2 视点名;不符 = 停判读)。

## 阶段 7 · 复验(160,分支代码)

```bash
uv run python -m ops.main doctor --family snapshot-stale --family timeline-drift ; echo "exit=$?"
# 预期两族均 0 findings,exit=0
docker exec ops-pg psql -U ops -d ops -c "SELECT discovery_method, count(*)
FROM factor_info GROUP BY 1;"          # 预期仅 automated/manual,无 NULL
docker exec ops-pg psql -U ops -d ops -c "\d factor_info" | grep -A1 discovery
# 预期 not null + CHECK IN ('automated','manual')
uv run python -m ops.main list 2>/dev/null | tail -1  # Total 8252 不变
uv run python -m ops.main list -u zxu 2>/dev/null | head -8   # 混排应出现 status 列
uv run python -m ops.main doctor ; echo "exit=$?"  # 全量:pool-ghost missing 少 ≈31
```

六段贴回。判读通过 → 我方提 PR;合并后四机 `git pull` +
`uv tool install --reinstall .`,台账三行 ⬜ → ✅。
