# Legacy 老因子档案清理批(2026-07-13 立项;用户点名"找个时间一起解决")

## 范围(输入清单)

| # | 项 | 数量 | 处置 |
|---|---|---|---|
| 1 | 历史 snapshot_at 漂移(v3 新判据存量发现:fguo 统一 16:38:27 批量、lhw 差 8h 时区) | 472 | `migrate_legacy_snapshot_at.sql`:snapshot_at := 最近 check 事件 at(factor_history 是真相源,v3 SSOT);幂等,前置计数 |
| 2 | compliance 被拒无快照(long_backtest 已跑完、pnl 在盘) | 22 | `scripts/postgres/backfill_compliance_snapshots.py`:补跑 simsummary → `repo.attach_snapshot(measured_at=失败 check 事件 at)`;dry-run 缺省 |
| 3 | `discovery_method='backfill'` + NULL 存量 | 待探 | **已拍板(2026-07-13)**:字段**不允许 NULL**,值按 **pnl 分流池位置**判定(pnl_automated → automated / pnl_manual → manual)。两池皆无/皆在的残余由**用户人工判定**("我知道哪些是机器因子哪些是人工因子"),经 `migrate_discovery_notnull.py --assign <名单>` 喂入(人工优先于池位置);全部落定后同事务收口 `chk_discovery` IN ('automated','manual') + `SET NOT NULL`。代码侧 DDL / init/01-schema.sql 已同改(test_schema_pin 钉住);`check._ensure_record` 对无 dm 的 staging 残留状态写入前显式拒绝(重新 submit 补全) |
| 4 | NULL 盘点(submitted_at / author='unknown') | 待探 | 盘点归档为主:submitted_at NULL 是 backfill 存量的**设计内值**(真实提交时间不可知),不伪造;author='unknown' 名单留档,可人工认领。discovery_method 的 NULL 并入项 3 处置 |
| 5 | `ops backfill` 命令 | — | **已拍板(2026-07-13):退役删除**(照 sync/health/refresh 先例)—— bootstrap 使命 2026-07-06 已完成,正常流程永不再补录;留着 = src 孤儿整批复活成 ACTIVE 的风险(doctor v1 警告过)。`HISTORY_OPS` 与 DB `chk_op` **保留** 'backfill' 枚举值(存量事件是历史事实,审计活过命令退役) |
| 6 | doctor 加 created_at 不变量对账 | — | 顺手项:`created_at <= submitted_at`(v3 词汇表不变量)进对账,防 07-10 那类批量写再犯不被察觉 |
| 7 | `ops list` 混排加 status 列 | — | **已拍板(2026-07-13):做,已实施** —— 结果集含被拒因子时表格显式插入 status 列(与 fail_stage 列同触发;纯 ACTIVE 列表不加,避免噪音;JSON 输出本就有 status 键) |

## 只读探针(执行者,判读输入)

```bash
# 3/4:discovery_method 分布 + NULL 盘点
docker exec ops-pg psql -U ops -d ops -c "SELECT discovery_method, count(*) FROM factor_info GROUP BY 1 ORDER BY 2 DESC;"
docker exec ops-pg psql -U ops -d ops -c "SELECT count(*) FILTER (WHERE s.submitted_at IS NULL) AS sub_null,
  count(*) FILTER (WHERE i.author IS NULL OR i.author='unknown') AS author_unknown
  FROM factor_info i LEFT JOIN factor_state s USING (name);"
# 1:472 漂移抽样(修正方向眼检:期望值 = 最近 check 事件 at)
docker exec ops-pg psql -U ops -d ops -c "
SELECT n.name, n.snapshot_at, lc.at AS expected FROM factor_snapshot n
JOIN (SELECT DISTINCT ON (name) name, at FROM factor_history WHERE op='check'
      ORDER BY name, at DESC, id DESC) lc USING (name)
WHERE n.snapshot_at IS DISTINCT FROM lc.at LIMIT 10;"
# 2:compliance 22 的 pnl 在盘率
docker exec ops-pg psql -U ops -d ops -At -c "
SELECT s.name FROM factor_state s LEFT JOIN factor_snapshot n USING (name)
JOIN LATERAL (SELECT failed_stage FROM factor_history h WHERE h.name=s.name
  AND h.op='check' AND h.passed=FALSE ORDER BY at DESC LIMIT 1) lf ON TRUE
WHERE s.status='rejected' AND n.name IS NULL AND lf.failed_stage='compliance'" \
| while read n; do [ -f /tank/vault/alphalib/alpha_pnl/$n ] && echo "$n Y" || echo "$n N"; done

# 3(拍板①判读输入):'backfill'/NULL 全量 × 池位置逐条盘点 → /tmp/dm-probe.txt
docker exec ops-pg psql -U ops -d ops -At -F'|' -c "
SELECT i.name, coalesce(i.discovery_method,'NULL'), coalesce(s.status,'no-state')
FROM factor_info i LEFT JOIN factor_state s USING (name)
WHERE i.discovery_method IS NULL OR i.discovery_method='backfill'
ORDER BY 2,3,1" | while IFS='|' read n dm st; do
  a=N; m=N
  [ -e /tank/vault/alphalib/pnl_automated/$n ] && a=Y
  [ -e /tank/vault/alphalib/pnl_manual/$n ] && m=Y
  echo "$n $dm $st auto=$a man=$m"
done | tee /tmp/dm-probe.txt | awk '{print $2, $3, $4"/"$5}' | sort | uniq -c | sort -rn
# 两池皆无的残余(须单独定规则)的 author 分布:
awk '$4=="auto=N" && $5=="man=N" {print $1}' /tmp/dm-probe.txt > /tmp/dm-unresolved.txt
docker exec -i ops-pg psql -U ops -d ops -c "
SELECT i.author, coalesce(s.status,'no-state') AS status, count(*)
FROM factor_info i LEFT JOIN factor_state s USING (name)
WHERE i.name = ANY(string_to_array('$(paste -sd, /tmp/dm-unresolved.txt)', ','))
GROUP BY 1,2 ORDER BY 3 DESC;"
```

## 拍板记录:--assign 映射(2026-07-13,探针判读后)

- **ybai / zxu / cchang / sli → manual(全量,含 submitted 队列 —— 不走
  cancel 捷径)**;**hwang → automated(全量)**;**fguo 2 条已点名
  (2026-07-13):AlphaFguo20260615GA267 / GA283 均 automated(机器因子)**。
  至此 129 条全部有值来源:8 池位置 + 119 author 映射 + 2 点名。
- 8 条池位置可判的(active,manual 池)不进名单,留给脚本按池判。
- 状态维度迁移无差别(身份属性统一 UPDATE);下游行为面三分:ACTIVE
  31 条池补账(手册阶段 6,**只补本批**,approve 豁免的 missing 不动);
  SUBMITTED 81 条档案先行(XML 仍缺字段,将来 check 回退全库池 + 不分流,
  入库后经 doctor missing → 补账闭环;不改 QR 的 XML);REJECTED 9 条纯档案。
- ~~遗留排查:zxu 40 条 NULL 疑似旧部署机器提交~~ **假设不成立**(2026-07-13
  执行者实测):129/129 NULL-dm 的 submit 事件 actor=migration,全是 v2b
  迁移合成的存量档案 —— 不存在正在产 NULL 的部署漂移,submit 硬校验有效。

执行手册:`docs/remediation/VERIFY-LEGACY-CLEANUP.md`。

## 实施顺序

1. ~~代码批~~ **已完成(2026-07-13)**:backfill 退役 + doctor timeline-drift 族 +
   list status 列 + discovery_method NOT NULL 代码侧 DDL 同改 + 三个迁移脚本
   (全部本地沙盘实测);
2. 探针(含 dm-probe)→ 判读 → unresolved 名单交用户人工判定(--assign 输入);
3. 执行者:备份 → 三脚本 dry-run → 判读 → apply → 复验
   (doctor snapshot-stale mismatch 472 → 0 + timeline-drift 0;
   discovery_method NULL/'backfill' → 0 + NOT NULL 收口 ✅);
   migrate_discovery_notnull 可分批:池位置先落,--assign 补齐后重跑收口;
4. PR + 四机滚存。

## 纪律沿用

迁移脚本 apply 用新连接查库验证持久化(v3 教训);dry-run 判读先行;
备份先行(info/snapshot 两表);全部幂等。

## 沙盘验证记录(2026-07-13,本地 PG 15433)

- `migrate_legacy_snapshot_at.sql`:五类种子全对 —— fguo 批量时刻漂移取**最新**
  check 事件(旧事件被 DISTINCT ON 跳过)、lhw 8h 时区漂移拉正、无事件 legacy
  锚 entered_at、unanchored 与正确行分毫未动;二跑 0 行幂等;601 行守卫触发
  异常整体回滚(数据未动)。判据与 doctor `_scan_snapshot_stale` 逐字对齐,
  修完 mismatch 必归零(后置断言不过即回滚)。
- `backfill_compliance_snapshots.py`:候选过滤对(correlation 被拒、已有快照
  均排除)、stub simsummary 指标解析对、apply 后**新直连**复核落库 1/1、
  snapshot_at 与失败 check 事件 at 逐时刻相等、meta.json 的 delay/fields/tables
  落齐、bcorr 组 NULL;二跑候选归零幂等;dry-run 零写入。
- `migrate_discovery_notnull.py`:池位置判定对(仅 automated 池 → automated、
  仅 manual 池 → manual、两池皆在/皆无 → unresolved 列名单);--assign 人工
  优先(与池位置冲突时警告并以人工为准)、名单外名字忽略并警告;残余未清零
  时只落数据、约束跳过;补名单重跑 → 残余 0 → 同事务收窄 chk_discovery +
  SET NOT NULL;三跑幂等(候选 0 仍自愈补挂约束);收口后 NULL 插入被 DB 拒。
