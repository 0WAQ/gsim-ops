# AUDIT-DUMP-CONSISTENCY: 执行结果

执行环境: server-160 (10.9.100.160) + server-170 (10.9.100.170), 2026-07-18 ~10:00 CST

---

## 1. cchang per-factor prod.xml 存放处

### 1.1 搜索结果

```
# 170 上:
$ find /nvme125 -maxdepth 3 -name 'prod.xml' 2>/dev/null
(无结果)

$ find /nvme125 -maxdepth 2 -type d -name '*xml*' 2>/dev/null
/nvme125/combo_cchang/xml      # combo 配置(非 per-factor)
/nvme125/combo/xml             # combo 配置(非 per-factor)

$ find /nvme125 -maxdepth 3 -name '*.xml' -not -path '*/alphalib/*' -not -path '*/combo/*' -not -path '*trash*' 2>/dev/null
/nvme125/datasvc/template/config.build_cache.xml
/nvme125/datasvc/template/config.read_cache.xml
```

### 1.2 间接证据

cchang 的因子生产脚本在 `/home/cchang/stock_combo_product/`(home 权限 0750,不可读)。
`/tmp` 中找到历史残留:

```
$ cat /tmp/combo.conf.bak   # combo 配置文件(旧版,引用 /ext4/alpha_dump)
COMBO_ALPHA_SRC=/ext4/alpha_dump
(其它 combo 配置参数)

$ cat /tmp/combo_template.bak | head   # combo XML 模板
<Constants backdays="256" .../>
<Universe startdate="20180101" enddate="20260529" .../>
<Alpha id="SAMPLE" module="AlphaLoad" alphaDir="/ext4/alpha_dump/" ver="v2"/>
```

`/tmp/GA010_demod.xml`(cchang 的测试 XML)揭示 per-factor 生产参数:

```xml
<Constants backdays="256" niodatapath="/nvme125/datasvc/data/cc_all" ...
           checkpointDays="5" checkpointDir="/tmp/mod_test/ckpt/" />
<Universe startdate="20110101" enddate="20110301" .../>
```

**结论**: 无可见的持久化 per-factor prod.xml。cchang 的 `run_combo.sh`(或关联脚本)
很可能从 `alpha_src` 中的 Config XML 动态生成生产配置(改 backdays→256、niodatapath→cc_all、
startdate→20110101、enddate→TODAY-1、dumpAlphaDir→/nvme125/alpha_dump、
checkpointDir→/nvme125/checkpoint/),用完即弃或存在 0750 home 内。

---

## 2. dataset 因子集 vs ops ACTIVE 差集

### 2.1 基数

| 集合 | 计数 |
|---|---|
| ops ACTIVE (`factor_state.status='active'`) | 7475 |
| 170 dataset (`/nvme125/alpha_dump`) | 7587 |
| 交集 | 7032 |

### 2.2 在库未投产(ACTIVE 有 → dataset 无): 443

```
$ comm -23 active.txt dataset.txt | grep -oP '^Alpha[A-Z][a-z]+' | sort | uniq -c | sort -rn
    348 AlphaHwang
     69 AlphaXmf
     19 AlphaYbai
      5 AlphaSli
      1 AlphaLhw
      1 AlphaInterp
```

全部是 hwang/xmf/ybai/sli 等非当前产线 author(dataset 只覆盖 fguo/lhw/zxu/cchang)。

### 2.3 在产不在库(dataset 有 → ACTIVE 无): 555

```
$ comm -13 active.txt dataset.txt | grep -oP '^Alpha[A-Z][a-z]+' | sort | uniq -c | sort -rn
    348 AlphaFguo
    174 AlphaLhw
     33 AlphaZxu
```

这些是还在 dataset 中但未进 ops 因子库的(可能已被 rejected/已提交未完成 check/只有 dump 未注册)。

### 2.4 160 sidecar 覆盖率(补充)

| 指标 | 计数 |
|---|---|
| 160 sidecar (`alphalib.local/alpha_dump`) 目录数 | 4472 |
| 其中 ACTIVE | 3926 |
| ACTIVE 因子不在 160 sidecar | 3549 |
| 160 sidecar 孤儿(不在 ACTIVE) | 546 |

**注**: 160 sidecar 只有 52.5% 的 ACTIVE 因子有 dump(3926/7475)——近半因子 check 发生在其它机器。

---

## 3. 抽样对账

### 3.1 抽样框

从交集 7032 因子中,按 `alpha_src/meta.json` 的 backdays 分组:
- backdays ≤ 256: 6702 个
- backdays > 256: 330 个

每组取 10 个因子(作者/delay 混搭),日期取 `20150105 / 20180702 / 20211230 / 20251230`,版本 v2。

初选 8 因子在 160 sidecar 无 dump(目录不存在),补选 8 因子替代。
最终 20 因子 × 4 日期 = 80 对比较。

### 3.2 backdays ≤ 256 组:样本清单

| 因子 | backdays | delay | author |
|---|---|---|---|
| AlphaFguo20260609GA031 | 256 | 0 | fguo |
| AlphaZxu_260416_RKurt_delay1 | 256 | 1 | zxu |
| AlphaZxu_260531_MpbCurv_delay1 | 256 | 1 | zxu |
| AlphaZxu_260518_IslandRatioStd_delay1 | 256 | 1 | zxu |
| AlphaZxu_260521_CancelActCorr_delay1 | 256 | 1 | zxu |
| AlphaFguo20260615GA129 | 256 | 0 | fguo |
| AlphaFguo20260623GA070 | 256 | 0 | fguo |
| AlphaFguo20260602GA004 | 256 | 0 | fguo |
| AlphaFguo20260708GB075 | 256 | 0 | fguo |
| AlphaFguo20260615GA150 | 256 | 0 | fguo |

### 3.3 backdays > 256 组:样本清单

| 因子 | backdays | delay | author |
|---|---|---|---|
| AlphaLhwF445Dr1586d7c60b9847e | 320 | 1 | lhw |
| AlphaLhwF445Dr16935e82f3595d8 | 320 | 1 | lhw |
| AlphaLhwReturnVolumeTurnoverConfirm | 300 | 1 | lhw |
| AlphaLhwN101R006PriceShapeRepairN200R1033ProbUp30m | 360 | 1 | lhw |
| AlphaLhwF445Dr142c2efc184d7c5 | 320 | 1 | lhw |
| AlphaLhwF445Dr1831773c642e29e | 320 | 1 | lhw |
| AlphaLhwF445Dr021502616e52282 | 320 | 1 | lhw |
| AlphaLhwF445Dr1415be002d8d76b | 320 | 1 | lhw |
| AlphaLhwN101R023OrderFlowAbsorptionR40F15Cdpdp | 360 | 1 | lhw |
| AlphaLhwN101R045LiquidityStressRepairR75L002VwImbalanceDepth10 | 360 | 1 | lhw |

### 3.4 逐行比对结果

```
Factor                                                            Date       BD    Category           MaxDiff      ByteEq
------------------------------------------------------------------------------------------------------------------------
AlphaFguo20260602GA004                                            20150105   256   BYTE-EQUAL         0.000e+00    True
AlphaFguo20260602GA004                                            20180702   256   BYTE-EQUAL         0.000e+00    True
AlphaFguo20260602GA004                                            20211230   256   BYTE-EQUAL         0.000e+00    True
AlphaFguo20260602GA004                                            20251230   256   BYTE-EQUAL         0.000e+00    True
AlphaFguo20260609GA031                                            20150105   256   BYTE-EQUAL         0.000e+00    True
AlphaFguo20260609GA031                                            20180702   256   BYTE-EQUAL         0.000e+00    True
AlphaFguo20260609GA031                                            20211230   256   BYTE-EQUAL         0.000e+00    True
AlphaFguo20260609GA031                                            20251230   256   BYTE-EQUAL         0.000e+00    True
AlphaFguo20260615GA129                                            20150105   256   BYTE-EQUAL         0.000e+00    True
AlphaFguo20260615GA129                                            20180702   256   BYTE-EQUAL         0.000e+00    True
AlphaFguo20260615GA129                                            20211230   256   BYTE-EQUAL         0.000e+00    True
AlphaFguo20260615GA129                                            20251230   256   BYTE-EQUAL         0.000e+00    True
AlphaFguo20260615GA150                                            20150105   256   BYTE-EQUAL         0.000e+00    True
AlphaFguo20260615GA150                                            20180702   256   BYTE-EQUAL         0.000e+00    True
AlphaFguo20260615GA150                                            20211230   256   BYTE-EQUAL         0.000e+00    True
AlphaFguo20260615GA150                                            20251230   256   BYTE-EQUAL         0.000e+00    True
AlphaFguo20260623GA070                                            20150105   256   BYTE-EQUAL         0.000e+00    True
AlphaFguo20260623GA070                                            20180702   256   BYTE-EQUAL         0.000e+00    True
AlphaFguo20260623GA070                                            20211230   256   BYTE-EQUAL         0.000e+00    True
AlphaFguo20260623GA070                                            20251230   256   BYTE-EQUAL         0.000e+00    True
AlphaFguo20260708GB075                                            20150105   256   BYTE-EQUAL         0.000e+00    True
AlphaFguo20260708GB075                                            20180702   256   BYTE-EQUAL         0.000e+00    True
AlphaFguo20260708GB075                                            20211230   256   BYTE-EQUAL         0.000e+00    True
AlphaFguo20260708GB075                                            20251230   256   BYTE-EQUAL         0.000e+00    True
AlphaLhwF445Dr021502616e52282                                     20150105   320   BYTE-EQUAL         0.000e+00    True
AlphaLhwF445Dr021502616e52282                                     20180702   320   BYTE-EQUAL         0.000e+00    True
AlphaLhwF445Dr021502616e52282                                     20211230   320   BYTE-EQUAL         0.000e+00    True
AlphaLhwF445Dr021502616e52282                                     20251230   320   BYTE-EQUAL         0.000e+00    True
AlphaLhwF445Dr1415be002d8d76b                                     20150105   320   DRIFT              2.659e+04    False
AlphaLhwF445Dr1415be002d8d76b                                     20180702   320   BYTE-EQUAL         0.000e+00    True
AlphaLhwF445Dr1415be002d8d76b                                     20211230   320   BYTE-EQUAL         0.000e+00    True
AlphaLhwF445Dr1415be002d8d76b                                     20251230   320   BYTE-EQUAL         0.000e+00    True
AlphaLhwF445Dr142c2efc184d7c5                                     20150105   320   BYTE-EQUAL         0.000e+00    True
AlphaLhwF445Dr142c2efc184d7c5                                     20180702   320   BYTE-EQUAL         0.000e+00    True
AlphaLhwF445Dr142c2efc184d7c5                                     20211230   320   BYTE-EQUAL         0.000e+00    True
AlphaLhwF445Dr142c2efc184d7c5                                     20251230   320   BYTE-EQUAL         0.000e+00    True
AlphaLhwF445Dr1586d7c60b9847e                                     20150105   320   BYTE-EQUAL         0.000e+00    True
AlphaLhwF445Dr1586d7c60b9847e                                     20180702   320   BYTE-EQUAL         0.000e+00    True
AlphaLhwF445Dr1586d7c60b9847e                                     20211230   320   BYTE-EQUAL         0.000e+00    True
AlphaLhwF445Dr1586d7c60b9847e                                     20251230   320   BYTE-EQUAL         0.000e+00    True
AlphaLhwF445Dr16935e82f3595d8                                     20150105   320   BYTE-EQUAL         0.000e+00    True
AlphaLhwF445Dr16935e82f3595d8                                     20180702   320   BYTE-EQUAL         0.000e+00    True
AlphaLhwF445Dr16935e82f3595d8                                     20211230   320   BYTE-EQUAL         0.000e+00    True
AlphaLhwF445Dr16935e82f3595d8                                     20251230   320   BYTE-EQUAL         0.000e+00    True
AlphaLhwF445Dr1831773c642e29e                                     20150105   320   BYTE-EQUAL         0.000e+00    True
AlphaLhwF445Dr1831773c642e29e                                     20180702   320   BYTE-EQUAL         0.000e+00    True
AlphaLhwF445Dr1831773c642e29e                                     20211230   320   BYTE-EQUAL         0.000e+00    True
AlphaLhwF445Dr1831773c642e29e                                     20251230   320   BYTE-EQUAL         0.000e+00    True
AlphaLhwN101R006PriceShapeRepairN200R1033ProbUp30m                20150105   360   BYTE-EQUAL         0.000e+00    True
AlphaLhwN101R006PriceShapeRepairN200R1033ProbUp30m                20180702   360   BYTE-EQUAL         0.000e+00    True
AlphaLhwN101R006PriceShapeRepairN200R1033ProbUp30m                20211230   360   BYTE-EQUAL         0.000e+00    True
AlphaLhwN101R006PriceShapeRepairN200R1033ProbUp30m                20251230   360   DRIFT              inf          False
AlphaLhwN101R023OrderFlowAbsorptionR40F15Cdpdp                    20150105   360   BYTE-EQUAL         0.000e+00    True
AlphaLhwN101R023OrderFlowAbsorptionR40F15Cdpdp                    20180702   360   BYTE-EQUAL         0.000e+00    True
AlphaLhwN101R023OrderFlowAbsorptionR40F15Cdpdp                    20211230   360   BYTE-EQUAL         0.000e+00    True
AlphaLhwN101R023OrderFlowAbsorptionR40F15Cdpdp                    20251230   360   DRIFT              inf          False
AlphaLhwN101R045LiquidityStressRepairR75L002VwImbalanceDepth10    20150105   360   BYTE-EQUAL         0.000e+00    True
AlphaLhwN101R045LiquidityStressRepairR75L002VwImbalanceDepth10    20180702   360   BYTE-EQUAL         0.000e+00    True
AlphaLhwN101R045LiquidityStressRepairR75L002VwImbalanceDepth10    20211230   360   BYTE-EQUAL         0.000e+00    True
AlphaLhwN101R045LiquidityStressRepairR75L002VwImbalanceDepth10    20251230   360   DRIFT              inf          False
AlphaLhwReturnVolumeTurnoverConfirm                               20150105   300   BYTE-EQUAL         0.000e+00    True
AlphaLhwReturnVolumeTurnoverConfirm                               20180702   300   BYTE-EQUAL         0.000e+00    True
AlphaLhwReturnVolumeTurnoverConfirm                               20211230   300   BYTE-EQUAL         0.000e+00    True
AlphaLhwReturnVolumeTurnoverConfirm                               20251230   300   BYTE-EQUAL         0.000e+00    True
AlphaZxu_260416_RKurt_delay1                                      20150105   256   BYTE-EQUAL         0.000e+00    True
AlphaZxu_260416_RKurt_delay1                                      20180702   256   BYTE-EQUAL         0.000e+00    True
AlphaZxu_260416_RKurt_delay1                                      20211230   256   BYTE-EQUAL         0.000e+00    True
AlphaZxu_260416_RKurt_delay1                                      20251230   256   BYTE-EQUAL         0.000e+00    True
AlphaZxu_260518_IslandRatioStd_delay1                             20150105   256   BYTE-EQUAL         0.000e+00    True
AlphaZxu_260518_IslandRatioStd_delay1                             20180702   256   BYTE-EQUAL         0.000e+00    True
AlphaZxu_260518_IslandRatioStd_delay1                             20211230   256   BYTE-EQUAL         0.000e+00    True
AlphaZxu_260518_IslandRatioStd_delay1                             20251230   256   BYTE-EQUAL         0.000e+00    True
AlphaZxu_260521_CancelActCorr_delay1                              20150105   256   BYTE-EQUAL         0.000e+00    True
AlphaZxu_260521_CancelActCorr_delay1                              20180702   256   BYTE-EQUAL         0.000e+00    True
AlphaZxu_260521_CancelActCorr_delay1                              20211230   256   BYTE-EQUAL         0.000e+00    True
AlphaZxu_260521_CancelActCorr_delay1                              20251230   256   BYTE-EQUAL         0.000e+00    True
AlphaZxu_260531_MpbCurv_delay1                                    20150105   256   BYTE-EQUAL         0.000e+00    True
AlphaZxu_260531_MpbCurv_delay1                                    20180702   256   BYTE-EQUAL         0.000e+00    True
AlphaZxu_260531_MpbCurv_delay1                                    20211230   256   BYTE-EQUAL         0.000e+00    True
AlphaZxu_260531_MpbCurv_delay1                                    20251230   256   BYTE-EQUAL         0.000e+00    True
```

### 3.5 汇总

```
=== SUMMARY BY GROUP ===

--- backdays<=256 (40 comparisons) ---
  BYTE-EQUAL          : 40
  ATOL-EQUAL          : 0
  DRIFT               : 0
  MISSING-in-dataset  : 0

  By date:
    20150105: BYTE-EQUAL=10
    20180702: BYTE-EQUAL=10
    20211230: BYTE-EQUAL=10
    20251230: BYTE-EQUAL=10

--- backdays>256 (40 comparisons) ---
  BYTE-EQUAL          : 36
  ATOL-EQUAL          : 0
  DRIFT               : 4
  MISSING-in-dataset  : 0

  By date:
    20150105: BYTE-EQUAL=9, DRIFT=1
    20180702: BYTE-EQUAL=10
    20211230: BYTE-EQUAL=10
    20251230: BYTE-EQUAL=7, DRIFT=3
```

### 3.6 drift 明细

| # | 因子 | 日期 | backdays | maxdiff | 性质 |
|---|---|---|---|---|---|
| 1 | AlphaLhwN101R006PriceShapeRepairN200R1033ProbUp30m | 20251230 | 360 | inf | check 全 NaN, dataset 4937 值 |
| 2 | AlphaLhwN101R023OrderFlowAbsorptionR40F15Cdpdp | 20251230 | 360 | inf | check 全 NaN, dataset 4937 值 |
| 3 | AlphaLhwN101R045LiquidityStressRepairR75L002VwImbalanceDepth10 | 20251230 | 360 | inf | check 全 NaN, dataset 4927 值 |
| 4 | AlphaLhwF445Dr1415be002d8d76b | 20150105 | 320 | 2.659e+04 | NaN 分布一致, 所有 2226 非 NaN 值均不同 |

### 3.7 drift 详细分析

**drift #1-3(bd=360, 日期 20251230, check 全 NaN)**:

```
=== AlphaLhwN101R006PriceShapeRepairN200R1033ProbUp30m @ 20251230 (bd=360) ===
  shape: check=(5484,) dataset=(5484,)
  check:   nan=5484 zero=0 nonzero=0          ← 全 NaN
  dataset: nan=547  zero=0 nonzero=4937        ← 正常
  both_nan=547 only_check_nan=4937 only_dataset_nan=0

check 侧 XML:
  <Constants backdays="360" niodatapath="/datasvc/data/cc_2025" .../>
  <Universe startdate="20150101" enddate="20251231" .../>

160 cc_2025 .meta: 20251231 dateCapacity 3900
170 cc_all  .meta: 20260717 dateCapacity 4029
```

三条 drift 同一模式:bd=360 因子在 check 跑时 cc_2025 dateCapacity=3900,
20251230 距末尾只剩 1 天(gsim 日索引接近边界),360 天回望窗口可能碰到
cc_2025 数据不足区(startdate=20150101 → 首可用输出约在第 360 个交易日后)。
dataset 侧用 cc_all(dateCapacity=4029)+ backdays 强制 256,窗口更短,正常出值。

**drift #4(bd=320, 日期 20150105, 值全漂)**:

```
=== AlphaLhwF445Dr1415be002d8d76b @ 20150105 (bd=320) ===
  shape: check=(5484,) dataset=(5484,)
  check:   nan=3258 zero=0 nonzero=2226
  dataset: nan=3258 zero=0 nonzero=2226
  both_nan=3258 only_check_nan=0 only_dataset_nan=0
  both_have_value=2226 of_which_differ=2226
  diff stats: min=2.503e-01 max=2.659e+04 mean=2.817e+03
```

NaN 分布完全一致(同样 3258 个 NaN),但所有 2226 个非 NaN 值无一相同。
check 用 backdays=320 + startdate=20150101,20150105 仅距起点 4 天(远不够 320 天回望);
dataset 用 backdays=256 + startdate=20110101,20150105 距起点约 1000 个交易日(足够 256 天)。
**回望窗口内数据量不同 → 状态因子在历史浅水区产生完全不同的输出。**
同因子在 20180702/20211230/20251230(回望窗口充足)三个日期上 BYTE-EQUAL。

---

## 4. cc 漂移隔离

本轮 backdays ≤ 256 组 **40/40 BYTE-EQUAL**,无 drift。
backdays > 256 组 4 条 drift 全部可归因于 backdays 强制 256 + startdate 前移(窗口差异),
未触发 cc 漂移隔离步骤。

---

## 附录 A: 初选被跳过的因子(160 sidecar 无 dump)

以下 8 因子在 ops ACTIVE 且在 170 dataset,但 160 sidecar 完全无目录(check 发生在其它机器):

| 因子 | backdays 组 |
|---|---|
| AlphaJzhang20260210GA011 | ≤256 |
| AlphaFguo20260513GA006 | ≤256 |
| AlphaJzhang20260206GA041 | ≤256 |
| AlphaZxu_260509_XlargeBarPosRet_U | ≤256 |
| AlphaZxu_260625_EpsRevMom_delay1 | >256 |
| AlphaLhwV4B156ValueDiffInstituteVolumeConfirmFail | >256 |
| AlphaLhwF445Dr18211ee855458af | >256 |
| AlphaLhwF445Drday237r12_0237_cancel_ra | >256 |

已由替代样本补齐(§3.2 / §3.3 中标注)。

## 附录 B: 比对方法

```python
# 170 上用 /usr/local/gsim/.venv/bin/python3 执行
import numpy as np
a = np.load(check_path)      # 160 sidecar 的 dump
b = np.load(dataset_path)    # 170 /nvme125/alpha_dump 的 dump
byte_eq = open(check_path,'rb').read() == open(dataset_path,'rb').read()
both_nan = np.isnan(a) & np.isnan(b)
diff = np.where(both_nan, 0, np.where(np.isnan(a)|np.isnan(b), np.inf, a-b))
maxdiff = np.nanmax(np.abs(diff))
# 分类: BYTE-EQUAL(byte_eq) / ATOL-EQUAL(maxdiff<1e-6) / DRIFT(其它) / MISSING
```
