# VERIFY · DOCTOR v1.1 处置执行结果

**分支** `claude/doctor-v11`,base c382de5。**host** server-160(PG=真相源)。
**日期** 2026-07-12。判读材料见 `DOCTOR-V11-TRIAGE.md`(四批 A/B/C/D 分桶 + 三项细核)。

四批孤儿(D 4 alien / B+C 62 feature / A+C 107 src),按优先级 D→B+C→A 逐批处置,
每批先贴材料等拍板再动。fast suite 前置 **164 passed**。全程零意外,复跑全库归零。

---

## D 批 · 4 alien 临时文件(人工 sudo rm)

pack 写产物中断的临时文件(`.vN.npy.<8hex>`),对应正式 npy 已在位完整 → 纯垃圾。
老板手动 `sudo rm`(owner root)。删前 `ls -la` 原文:

```
-rw-r--r-- root alpha-data 75759616 Jun 4 09:48 AlphaJzhang20260316GA003.v2.npy.318CBB27
-rw-r--r-- root alpha-data 67371008 Jun 4 09:48 AlphaJzhang20260316GA005.v1.npy.2C1fBC7d
-rw-r--r-- root alpha-data 50593792 Jun 4 09:47 AlphaJzhang20260316GA005.v2.npy.0f5B4bD4
-rw-r--r-- root alpha-data 75759616 Jun 4 09:48 AlphaJzhang20260316GA008.v1.npy.31B47Efc
```

对应正式文件(TRIAGE ②,均在位 171MB):`AlphaJzhang20260316GA003.v2.npy` /
`GA005.v1.npy` / `GA005.v2.npy` / `GA008.v1.npy`。

**结果**:4 删净,后续 doctor 扫描 alien 归零。

---

## B+C 批(feature 侧)· 62 feature 文件(doctor --fix)

31 因子 × (v1+v2) = 62,全 `AlphaYbai` 短日期名。fix 前只读清单核对:
`fixable=62`,全短日期 ybai,无异常前缀,无第 63 条。

`uv run ops doctor --fix artifact-orphan -y`(材料前置的非 TTY 流程,-y 只授权
artifact-orphan 一族)输出 62 条全部"已修复",无一 skip/error。

复跑归零:

```
 family            scope    checked   fail   warn   fixable   fixed   locked
 artifact-orphan   global     24179      0      0         0       0        0
```

**记账自洽**:checked 24241(fix 前)→ 24179(fix 后)= 降 62,即删掉的 62 个 feature
文件不再纳入扫描。(D 批 4 alien 已在 fix 前删除,故起点 24241 = 原 24245 − 4。)

---

## A+C 批(src 侧)· 107 src-orphan 目录(cleanup 脚本)

alpha_src 是源码唯一副本、爆炸半径最大 —— doctor v1 铁律 alpha_src 永不进删除集,
故走名单化一次性脚本 `scripts/cleanup_src_orphans.py`(判读后处置,非常规通道)。
逐目录守卫:factor_lock 非阻塞 → 锁内 repo.get 复核 PG 仍无记录 → 名字不在 staging →
目标是真实目录非软链 → rmtree。

导报告 + dry-run(只读,无需 sudo):

```
uv run ops doctor --format json > /tmp/doctor-v11.json 2>/dev/null   # src-orphan 107
uv run python scripts/cleanup_src_orphans.py --input /tmp/doctor-v11.json
```

dry-run 输出 107 行全部 `would-remove`,汇总 `{'would-remove': 107}`,零 skip 零 error。
文件数与 TRIAGE 分桶一致(Fguo/fguo 5-6、hwang 5、ybai 4、zxu 4)。

`--apply`(老板 sudo 执行,锁内 PG 复核兜底):**107/107 removed,零 skip 零 error**。

**边界确认**:被删的 `AlphaZxu_260414_VOV_delay1`(带 `_delay1`)与在库 ACTIVE 的
`AlphaZxu_260414_VOV`(无后缀)是两个名字,在库因子未受波及;apply 时刻锁内 PG 复核
亦兜此层。C 批 5 个 ybai 两面(长名 src + 短名 feature)按长/短两名分别清,两面均净。

---

## 收单 · 全库复跑归零汇总表

`uv run ops doctor`(EXIT=0):

```
 family            scope    checked   fail   warn   fixable   fixed   locked   note
 ─────────────────────────────────────────────────────────────────────────────────
 pool-ghost        global      7433      0      8         0       0        0   bcorr 池副本 ⇔ ACTIVE 在库
 snapshot-stale    pg          7472      0      0         0       0        0   入库快照 snapshot_at ⇔ entered_at
 info-orphan       pg          8419      0      0         0       0        0   factor_info ⇔ factor_state 成对
 src-drift         global      8289      0      0         0       0        0   alpha_src 目录 ⇔ PG 在库集
 staging-drift     global       167      0      0         0       0        0   staging 目录 ⇔ factor_state
 artifact-orphan   global     24179      0      0         0       0        0   alpha_pnl / alpha_feature ⇔ factor_info
 dump-orphan       host        4472      0      0         0       0        0   本机 dump sidecar ⇔ factor_info
```

**src-drift / artifact-orphan / snapshot-stale / info-orphan / staging-drift /
dump-orphan 全 0;fail 全 0;EXIT=0**。仅剩 pool-ghost 8 条合法 WARN:

```
  AlphaSli_260605_ExLgPinCombo_delay1 — ACTIVE 缺 pnl_manual 池副本(approve 豁免属合法;瞬态:archive 拷贝中)
  AlphaXmfRpt0524W10CrowdedTailFade60M30S8D6IndD1Long — ACTIVE 缺 pnl_automated 池副本
  AlphaXmfRpt1938Mom126Rev5ResonanceD8 — ACTIVE 缺 pnl_automated 池副本
  AlphaYbai20260626AsymVolatility14 — ACTIVE 缺 pnl_manual 池副本
  AlphaYbai_0630_EarningsSurpriseSUE — ACTIVE 缺 pnl_manual 池副本
  AlphaYbai_0630_PLSLatentT180D3 — ACTIVE 缺 pnl_manual 池副本
  AlphaYbai_0701_CJIdioRateL21D3 — ACTIVE 缺 pnl_manual 池副本
  AlphaYbai_0702_BETCAGRStd — ACTIVE 缺 pnl_manual 池副本
```

pool-ghost 属 approve 豁免 + archive 拷贝瞬态,设计内不可修(只报不删),非漂移。

---

## 收单结论

| 批 | 手段 | 计数 | 意外 |
|---|---|---|---|
| D | 人工 sudo rm | 4 | 0 |
| B+C(feature) | doctor --fix | 62 | 0 |
| A+C(src) | cleanup 脚本 --apply | 107 | 0 |
| **合计** | | **173** | **0** |

全库 doctor 除 pool-ghost 8 条合法 WARN 外全归零,EXIT=0。v1.1 处置达标,可关单。

L1(meta 写入侧校验缺口)/ L2(ybai 长短双命名)挂账仍在,为写入侧根因,留后续。
