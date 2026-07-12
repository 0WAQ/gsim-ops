# DOCTOR v1.1 处置判读材料(src-orphan / artifact-orphan)

**分支** `claude/doctor-v11`。快照源 `ops doctor --format json`(server-160,PG=真相源)。
全程**只读**采集,未动任何盘面/PG 数据。本文件是处置拍板的正式材料 —— doctor `--fix`
落哪些族、按什么顺序、哪些先跟人确认,以此为准。

**一句话结论**:表面上是两族孤儿(107 src-orphan + 66 artifact-orphan),实际是
**四批相互独立、成因不同的残留**,不能当"同一次清理的多个面"一把梭 `--fix`。其中
src-orphan 一族混着在职 QR 与一周内的因子,**不可无条件修复**;ybai 长/短双命名会让
doctor 把"同一因子的两面"误判成两条独立孤儿。

---

## 采集口径

| 族(doctor family) | kind | 计数 | 盘面位置 |
|---|---|---|---|
| `src-drift` | `src-orphan` | 107 | `alpha_src/<name>/`(src 目录在,PG 无 record) |
| `artifact-orphan` | `feature-orphan` | 62 | `alpha_feature/<name>.vN.npy`(feature 在,无对应因子) |
| `artifact-orphan` | `alien` | 4 | `alpha_feature/<name>.vN.npy.<8hex>`(带临时后缀的写残留) |

62 feature-orphan = **31 个因子 × (v1+v2)**。

---

## 批次划分(四批,互不重叠)

| 批 | 来源 | 计数 | 成因 | 盘面侧影 | 处置建议 |
|---|---|---|---|---|---|
| A | src-orphan | 107 | src-only 残留,PG 无 record,**无任何 pnl/feature/dump 侧影** | 三产物侧影全 0 | **不可无条件 fix**,按 author 分桶 + 逐条 PG 二次核对 |
| B | feature-orphan(27) | 27 因子 | 真·命名迁移遗留:因子已用**长名**在库,旧**短名** feature 成孤儿 | 长名在 PG | 相对干净,可优先 fix(删短名 feature) |
| C | ybai 双命名两面孤儿 | 5 因子 | 同一批 ybai 因子长名 src + 短名 feature **两面都没删净**,doctor 因长/短名不等未配对 | 两名皆无 PG record | 两面一起删,删除逻辑必须覆盖长+短两种名 |
| D | alien | 4 | pack/写产物中断的临时文件,正式 `.npy` 已在位 | 对应正式文件在 | **纯垃圾,最安全**,可优先 fix |

> ④ 的"交集 0"是**长名 vs 短名严格字符串不等造成的假阴性**;换算命名后 C 批浮现
> (见下"③ ybai 双命名对照")。这是本轮判读最关键的修正 —— 先前"两批完全独立"的
> 结论对 ybai 这 5 个不成立。

---

## A 批:107 src-orphan(按 author 分桶)

**共性**:全部有 `meta.json`(无 meta = 0);每目录 4-6 文件(完整因子目录);
**③ 交叉核对确认无任何 pnl / feature / dump 侧影**(三项全 0)。即"只剩 src 目录、
PG 无 record、也从没留下产物"的孤儿 —— rm/cancel 删了 PG 记录与产物但 src 目录没删净,
或从没 submit 进 PG。

分布:`Fguo 51 + fguo 3 + hwang 45 + ybai 5 + zxu 3`;mtime 分布 `2026-06 × 103,
2026-07 × 4`。

### 需单独标出的异常(修复前必须处理)

1. **在职活跃 QR 的残留**:Fguo、zxu 在最近 check run 里活跃(仓库 `docs/reports/check/`
   有 `check-fguo-20260709`、`check-zxu-20260710`)。这批不是"离职遗留",是**在职人的
   清理残留** —— 删前值得跟本人确认是否还要重提。
2. **mtime 一周内的 4 个 ybai**(`HTAlpha13/16/44/55`,mtime 2026-07-03):距今约一周,
   已逐个核 PG + staging(见下 ①),**确认 PG 无 record 且不在 staging → 是死孤儿,
   非活因子误判**。可随 A 批处置,但因新,建议仍知会 ybai。
3. **`Fguo`/`fguo` author 大小写分裂**(51 vs 3):同一人,meta.json 写入侧未归一 →
   独立挂账 L1。
4. **`AlphaZxu_260414_VOV_delay1` birthday=20061219**(2006 年,其余全 2026):明显错值;
   且 zxu 三个都是下划线命名 `AlphaZxu_260414_...`,与全库驼峰规范不符 → 独立挂账 L1。

### 分桶明细(107 行)

#### Fguo (51)
| name | birthday | dir_mtime |
|---|---|---|
| AlphaFguo20260428GA004 | 20260428 | 2026-06-04 |
| AlphaFguo20260428GA005 | 20260428 | 2026-06-04 |
| AlphaFguo20260428GA006 | 20260428 | 2026-06-04 |
| AlphaFguo20260428GA007 | 20260428 | 2026-06-04 |
| AlphaFguo20260428GA008 | 20260428 | 2026-06-04 |
| AlphaFguo20260428GA009 | 20260428 | 2026-06-04 |
| AlphaFguo20260428GA010 | 20260428 | 2026-06-04 |
| AlphaFguo20260428GA011 | 20260428 | 2026-06-04 |
| AlphaFguo20260428GA012 | 20260428 | 2026-06-04 |
| AlphaFguo20260428GA013 | 20260428 | 2026-06-04 |
| AlphaFguo20260428GA014 | 20260428 | 2026-06-04 |
| AlphaFguo20260428GA015 | 20260428 | 2026-06-04 |
| AlphaFguo20260428GA016 | 20260428 | 2026-06-04 |
| AlphaFguo20260430GA001 | 20260430 | 2026-06-04 |
| AlphaFguo20260430GA002 | 20260430 | 2026-06-04 |
| AlphaFguo20260430GA003 | 20260430 | 2026-06-04 |
| AlphaFguo20260430GA004 | 20260430 | 2026-06-04 |
| AlphaFguo20260430GA005 | 20260430 | 2026-06-04 |
| AlphaFguo20260430GA006 | 20260430 | 2026-06-04 |
| AlphaFguo20260430GA007 | 20260430 | 2026-06-04 |
| AlphaFguo20260430GA008 | 20260430 | 2026-06-04 |
| AlphaFguo20260430GA009 | 20260430 | 2026-06-04 |
| AlphaFguo20260430GA010 | 20260430 | 2026-06-04 |
| AlphaFguo20260430GA011 | 20260430 | 2026-06-04 |
| AlphaFguo20260430GA012 | 20260430 | 2026-06-04 |
| AlphaFguo20260430GA013 | 20260430 | 2026-06-04 |
| AlphaFguo20260430GA014 | 20260430 | 2026-06-04 |
| AlphaFguo20260430GA016 | 20260430 | 2026-06-04 |
| AlphaFguo20260430GA017 | 20260430 | 2026-06-04 |
| AlphaFguo20260430GA018 | 20260430 | 2026-06-04 |
| AlphaFguo20260430GA019 | 20260430 | 2026-06-04 |
| AlphaFguo20260430GA020 | 20260430 | 2026-06-04 |
| AlphaFguo20260430GA021 | 20260430 | 2026-06-04 |
| AlphaFguo20260503GA001 | 20260503 | 2026-06-04 |
| AlphaFguo20260503GA002 | 20260503 | 2026-06-04 |
| AlphaFguo20260503GA003 | 20260503 | 2026-06-04 |
| AlphaFguo20260503GA004 | 20260503 | 2026-06-04 |
| AlphaFguo20260503GA005 | 20260503 | 2026-06-04 |
| AlphaFguo20260503GA006 | 20260503 | 2026-06-04 |
| AlphaFguo20260503GA007 | 20260503 | 2026-06-04 |
| AlphaFguo20260503GA008 | 20260503 | 2026-06-04 |
| AlphaFguo20260503GA009 | 20260503 | 2026-06-04 |
| AlphaFguo20260503GA010 | 20260503 | 2026-06-04 |
| AlphaFguo20260503GA011 | 20260503 | 2026-06-04 |
| AlphaFguo20260503GA012 | 20260503 | 2026-06-04 |
| AlphaFguo20260503GA013 | 20260503 | 2026-06-04 |
| AlphaFguo20260503GA014 | 20260503 | 2026-06-04 |
| AlphaFguo20260503GA015 | 20260503 | 2026-06-04 |
| AlphaFguo20260503GA016 | 20260503 | 2026-06-04 |
| AlphaFguo20260503GA017 | 20260503 | 2026-06-04 |
| AlphaFguo20260503GA018 | 20260503 | 2026-06-04 |

#### fguo (3) — 与上桶同一人,author 小写
| name | birthday | dir_mtime |
|---|---|---|
| AlphaFguo20260528LLM006 | 20260528 | 2026-06-09 |
| AlphaFguo20260603LLM001 | 20260603 | 2026-06-09 |
| AlphaFguo20260603LLM002 | 20260603 | 2026-06-09 |

#### hwang (45)
| name | birthday | dir_mtime |
|---|---|---|
| AlphaHwangD0I12Neg | 20260525 | 2026-06-17 |
| AlphaHwangD0I24Decay8 | 20260601 | 2026-06-17 |
| AlphaHwangD0I26Neg | 20260522 | 2026-06-17 |
| AlphaHwangD0I31NegRescue | 20260521 | 2026-06-17 |
| AlphaHwangD0I34Decorr | 20260525 | 2026-06-17 |
| AlphaHwangD0I34Neg | 20260521 | 2026-06-17 |
| AlphaHwangD0I37RescueDecay8 | 20260525 | 2026-06-17 |
| AlphaHwangD0I43NegDecay8 | 20260527 | 2026-06-17 |
| AlphaHwangD0I80 | 20260601 | 2026-06-17 |
| AlphaHwangD0I80Decorr | 20260601 | 2026-06-17 |
| AlphaHwangD10I69NegDecay8 | 20260527 | 2026-06-17 |
| AlphaHwangD11I42 | 20260527 | 2026-06-17 |
| AlphaHwangD11I82NegRescueDecay8 | 20260526 | 2026-06-17 |
| AlphaHwangD13I64Neg | 20260527 | 2026-06-17 |
| AlphaHwangD14I33NegDecay8 | 20260527 | 2026-06-17 |
| AlphaHwangD15I6NegDecay8 | 20260527 | 2026-06-17 |
| AlphaHwangD18I57Decay10 | 20260527 | 2026-06-17 |
| AlphaHwangD18I5NegDecay8 | 20260527 | 2026-06-17 |
| AlphaHwangD18I61Neg | 20260526 | 2026-06-17 |
| AlphaHwangD18I8 | 20260526 | 2026-06-17 |
| AlphaHwangD19I39NegDecay8 | 20260601 | 2026-06-17 |
| AlphaHwangD19I49NegDecay8 | 20260601 | 2026-06-17 |
| AlphaHwangD19I99Decay10Decorr | 20260601 | 2026-06-17 |
| AlphaHwangD19I99NegDecay15 | 20260601 | 2026-06-17 |
| AlphaHwangD20I110NegDecay8DecorrDecay8Decay10 | 20260601 | 2026-06-17 |
| AlphaHwangD21I10 | 20260526 | 2026-06-17 |
| AlphaHwangD21I4 | 20260528 | 2026-06-17 |
| AlphaHwangD21I64NegDecay15Decorr | 20260527 | 2026-06-17 |
| AlphaHwangD23I103Neg | 20260601 | 2026-06-17 |
| AlphaHwangD25I68 | 20260528 | 2026-06-17 |
| AlphaHwangD38I81Neg | 20260528 | 2026-06-17 |
| AlphaHwangD3I106Decay8 | 20260528 | 2026-06-17 |
| AlphaHwangD3I34NegDecay8 | 20260527 | 2026-06-17 |
| AlphaHwangD3I35Neg | 20260520 | 2026-06-17 |
| AlphaHwangD3I6 | 20260527 | 2026-06-17 |
| AlphaHwangD42I2NegDecay8 | 20260531 | 2026-06-17 |
| AlphaHwangD45I5Decay8Decorr | 20260601 | 2026-06-17 |
| AlphaHwangD45I5NegDecay8 | 20260601 | 2026-06-17 |
| AlphaHwangD48I68Decay8 | 20260601 | 2026-06-17 |
| AlphaHwangD4I79NegDecay8 | 20260527 | 2026-06-17 |
| AlphaHwangD4I84Neg | 20260601 | 2026-06-17 |
| AlphaHwangD54I14NegDecay8 | 20260601 | 2026-06-17 |
| AlphaHwangD54I97NegDecay8 | 20260527 | 2026-06-17 |
| AlphaHwangD57I40Decay5Decay8 | 20260528 | 2026-06-17 |
| AlphaHwangD8I98 | 20260601 | 2026-06-17 |

#### ybai (5) — 见 C 批,与短名 feature-orphan 两面配对
| name | birthday | dir_mtime |
|---|---|---|
| AlphaYbai20260623HotReactionDaily | 20260623 | 2026-06-26 |
| AlphaYbai20260626HTAlpha13 | 20260626 | 2026-07-03 |
| AlphaYbai20260626HTAlpha16 | 20260626 | 2026-07-03 |
| AlphaYbai20260626HTAlpha44 | 20260626 | 2026-07-03 |
| AlphaYbai20260626HTAlpha55 | 20260626 | 2026-07-03 |

#### zxu (3) — 下划线命名 + birthday 错值
| name | birthday | dir_mtime |
|---|---|---|
| AlphaZxu_260414_VOV_delay1 | 20061219 ⚠ | 2026-06-08 |
| AlphaZxu_260418_CancelImb_delay1 | 20260418 | 2026-06-08 |
| AlphaZxu_260423_BigPeakRetailFollow_delay1 | 20260423 | 2026-06-08 |

---

## B 批:27 个 feature-orphan(真·命名迁移遗留)

短名 feature 文件,换算长名后**长名在 PG 在库** → 因子已用长名活着,旧短名 feature
是纯孤儿。全部 `AlphaYbai`、171.1MB、mtime 全 2026-07-01(一次性批量产出)。

删除对象是 `alpha_feature/<短名>.v1.npy` + `.v2.npy`,不涉及在库长名因子。可优先 fix。

---

## C 批:5 个 ybai 双命名两面孤儿(本轮关键发现)

同一批因子**长名 src 孤儿(A 批 ybai 5 条)+ 短名 feature 孤儿两面都在**,且**长名短名
在 PG 皆无 record**。doctor 因长/短名严格不等,未把两面配成一条,分别记进 src-drift 与
artifact-orphan。

| 短名(feature 侧) | 长名(src 侧) | PG |
|---|---|---|
| AlphaYbai0623HotReactionDaily | AlphaYbai20260623HotReactionDaily | 两名皆无 |
| AlphaYbai0626HTAlpha13 | AlphaYbai20260626HTAlpha13 | 两名皆无 |
| AlphaYbai0626HTAlpha16 | AlphaYbai20260626HTAlpha16 | 两名皆无 |
| AlphaYbai0626HTAlpha44 | AlphaYbai20260626HTAlpha44 | 两名皆无 |
| AlphaYbai0626HTAlpha55 | AlphaYbai20260626HTAlpha55 | 两名皆无 |

**处置要点**:这 5 个删除逻辑必须**同时覆盖长名 src 目录 + 短名 feature 文件**,按单一
名字删会漏掉另一面。→ 独立挂账 L2(ybai 双命名)。

---

## D 批:4 个 alien(临时文件残留,最安全)

`alpha_feature/<name>.vN.npy.<8hex>`,50-75MB,mtime 2026-06-04。pack/写产物时带临时
后缀写入、写完应 rename 覆盖正式名却中断。② 已确认**对应正式 `.npy` 全部在位且完整
(171MB)** → 临时文件是纯垃圾,可优先安全删除。

| 临时文件 | 对应正式文件 | 正式文件状态 |
|---|---|---|
| AlphaJzhang20260316GA003.v2.npy.318CBB27 | AlphaJzhang20260316GA003.v2.npy | 在位 171MB ✔ |
| AlphaJzhang20260316GA005.v1.npy.2C1fBC7d | AlphaJzhang20260316GA005.v1.npy | 在位 171MB ✔ |
| AlphaJzhang20260316GA005.v2.npy.0f5B4bD4 | AlphaJzhang20260316GA005.v2.npy | 在位 171MB ✔ |
| AlphaJzhang20260316GA008.v1.npy.31B47Efc | AlphaJzhang20260316GA008.v1.npy | 在位 171MB ✔ |

---

## 三项细核原文

### ① 4 个 mtime 最新 ybai src-orphan(2026-07-03)PG + staging

```
--- AlphaYbai20260626HTAlpha13 --- 未找到因子 / staging: 无
--- AlphaYbai20260626HTAlpha16 --- 未找到因子 / staging: 无
--- AlphaYbai20260626HTAlpha44 --- 未找到因子 / staging: 无
--- AlphaYbai20260626HTAlpha55 --- 未找到因子 / staging: 无
```
→ 全部 PG 无 record 且不在 staging,是死孤儿,非活因子误判。

### ② 4 个 alien 对应正式 .npy 是否在位

```
-rw-r--r-- root alpha-data 171100800 Jun 2 16:32 AlphaJzhang20260316GA003.v2.npy
-rw-r--r-- root alpha-data 171100800 Jun 2 16:32 AlphaJzhang20260316GA005.v1.npy
-rw-r--r-- root alpha-data 171100800 Jun 2 16:32 AlphaJzhang20260316GA005.v2.npy
-rw-r--r-- root alpha-data 171100800 Jun 2 16:32 AlphaJzhang20260316GA008.v1.npy
```
→ 4 个正式文件全在位完整 → 临时文件纯垃圾。

### ③ ybai 双命名对照(31 短名 → 长名 PG)

31 个短名 feature-orphan 换算长名后:**27 长名在库 + 4 两名皆无**。"两名皆无"的
`0623HotReactionDaily / 0626HTAlpha13/16/44/55` 与 A 批 5 个 ybai src-orphan 逐一对应
(=C 批)。其余 27 个为 B 批(长名在库,短名 feature 孤儿)。

> 注:短名去重 31,但 A 批 ybai 只 5 个;差额 26 = B 批(27 因子里 `0623HotReactionDaily`
> 同时落 C,故 B=26 长名在库 + 1 与 C 重叠命名,实际 B 批纯迁移遗留 26 个 feature 因子)。
> 拍板删除时以"长名是否在 PG"为唯一判据:在库→B(删短名 feature);不在→C(删两面)。

---

## 处置优先级建议(供拍板)

| 顺序 | 批 | 风险 | 前置条件 |
|---|---|---|---|
| 1 | D(4 alien) | 最低 | 无(正式文件已确认在位) |
| 2 | B(26 短名 feature) | 低 | 无(长名因子在库,删的是旧孤儿产物) |
| 3 | C(5 ybai 两面) | 中 | 删除逻辑覆盖长+短两名;建议知会 ybai |
| 4 | A(107 src-orphan) | **高,不可无条件 fix** | 按 author 分桶;Fguo/zxu 在职需确认;逐条 PG 二次核对 |

---

## 处置结果(2026-07-12 执行,分支 claude/doctor-v11)

按上表优先级 D→B+C→A 逐批执行,每批"先贴材料等拍板再动",全程零意外。执行原文摘录
见 `VERIFY-DOCTOR-V11-RESULT.md`。

| 批 | 对象 | 手段 | 计数 | 结果 |
|---|---|---|---|---|
| D | 4 alien 临时文件 | 人工 `sudo rm`(老板) | 4 | 全删;对应正式 npy 在位 |
| B+C(feature 侧) | 62 feature 文件(31 因子 × v1+v2) | `ops doctor --fix artifact-orphan -y` | 62 | 62/62 已修复,零 skip/error;checked 24241→24179 |
| A+C(src 侧) | 107 src-orphan 目录 | `scripts/cleanup_src_orphans.py --apply`(sudo,锁内 PG 复核) | 107 | 107/107 removed,零 skip/error |

**复跑归零**(`ops doctor` 全库,EXIT=0):src-drift / artifact-orphan / snapshot-stale /
info-orphan / staging-drift / dump-orphan 全 0;仅剩 **pool-ghost 8 条合法 WARN**
(approve 豁免 + archive 瞬态,设计内不可修)。

**边界确认**:C 批 5 个 ybai 两面(长名 src + 短名 feature)按长/短两名分别处置(feature 走
B+C fix、src 走 cleanup 脚本),两面均清。删除 `AlphaZxu_260414_VOV_delay1` 时确认与在库
ACTIVE 的 `AlphaZxu_260414_VOV`(无 `_delay1` 后缀)是两个名字,在库因子未受波及 —— apply
时刻锁内 PG 复核亦兜此层。

L1 / L2 挂账仍在(写入侧根因,非本轮处置)。

---

## 独立挂账(非本轮处置,写入侧根因)

- **L1 · meta.json 写入侧校验缺口**:`author` 大小写未归一(`Fguo`/`fguo` 同一人分裂);
  `birthday` 无合法性校验(zxu `20061219` 明显错值);zxu 下划线命名逃过全库驼峰约定。
  → submit 侧应在写 meta.json 时归一 author、校验 birthday 落在合理区间、校验因子名规范。
- **L2 · ybai 长/短双命名**:同一因子存在 `AlphaYbai20260623X`(长)与 `AlphaYbai0623X`
  (短)两套命名,横跨 src 与 feature 两侧产物,导致 doctor 无法把同一因子的两面配对,
  孤儿被拆成两条独立 finding。→ 命名规范应单一化;doctor 侧可考虑长/短名归一后再比对,
  避免此类假阴性。
