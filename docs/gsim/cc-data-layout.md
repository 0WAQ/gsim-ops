# cc 数据层物理布局

面向因子挖掘 / 数据探索的视角,描述 `/datasvc/data/` 下数据的物理形态、形状、填充进度、时间快照机制,以及不经过 gsim 直接用 `np.memmap` 读取的方法。

如果你在 gsim 框架内开发因子(`dr.getData('xxx')`),请看 [data-sources.md](data-sources.md);本文档面向"绕开 gsim 直接做 ML / 因子挖掘"的场景。

## 数据分层

公司内部把数据按 build 来源分为四层,每一层基于"当前层或上一层"的数据生成:

```
rawdata  ──→  cc (common cache)  ──→  dm (data manager)  ──→  L2-feature
   ↓               ↓                       ↓                       ↓
原始数据源        通用缓存                数据管理层               L2 衍生特征层
(wind/datayes/   /datasvc/data/cc_all   Dmgr_* (gsim 风格)       cn_equity_feature/*
 citics)         /datasvc/data/cc_*                              cn_equity_feature_5min/*
                                                                  realtime/* delta/*
```

| 层 | 物理位置 | 谁产出 |
|---|---|---|
| `rawdata` | `/datasvc/rawdata/{rawdata_wind, rawdata_datayes, rawdata_citics, rawdata_datayes_unused}` | 外部数据商 / 落盘脚本 |
| `cc` | `/datasvc/data/{cc, cc_2024, cc_2025, cc_all}` | rawdata 的标准化 cache |
| `dm` | cc_all 下 `Dmgr*` 前缀目录 | gsim DataManager 模块,基于 cc 算出来的衍生(行业聚合、市场指标等) |
| `L2-feature` | cc_all 下 `cn_equity_feature*` / `realtime` / `delta` | 我们自己基于 L2 数据做的各频率特征 |

> **命名注意**: 这里"L2-feature 层"指的是上游 L2 数据衍生的市场特征 (例如 `cn_equity_feature/fguo_max/*.npy`), **不要跟 `/tank/vault/alphalib/alpha_feature/`** (`ops pack` 输出的因子矩阵) 混淆 —— 后者是"alpha-feature", 词面相近但完全不同。

`cc_all` 是 cache 层的全集,既存了 rawdata 的镜像,也吸纳了 dm 层和 L2-feature 层的产出 —— 所以它是个混合层,但对外用 memmap 读起来形态一致。

## 数据源映射(rawdata → cc)

每个 cc_all 子目录都能追溯到 `/datasvc/rawdata/` 下的某个源:

| 数据源 | 路径 | 内容 |
|---|---|---|
| **Wind** | `rawdata_wind/` | 行情、财务三大表、分析师一致预期(rolling)、资金流、指数权重、ST、行业分类 |
| **通联(Datayes)** | `rawdata_datayes/` | 424 因子、精品因子(equ_fancy)、AI 盈利预测(*_fore_annual / _fore_quarter)、行业景气、行业因子 |
| **中信(Citics)** | `rawdata_citics/` | 5min 行情(`Interval5m`) |
| **Datayes 弃用** | `rawdata_datayes_unused/` | h2l 高频转低频因子、hf_daily_* 高频衍生表 —— **已停更**,没有增量 |

## 内部命名对照

| 内部叫法 | cc_all 目录前缀 | 字段数 |
|---|---|---|
| **424 因子** | `equ_factor_*`(15 表) | 433 |
| **精品因子** | `equ_fancy_factors_table1~10` | 208 |
| **AI 盈利预测** | `*_fore_annual` / `*_fore_quarter`(9 表) | 213 |
| **分析师预测** | `ashareconsensusrollingdata_*`(8 个口径) | 136 |
| **高频转低频因子** | `equ_h2l_factor_t1~t4`(已停更) | 122 |
| **高频衍生表** | `hf_daily_der_table_1~9` + `hf_daily_auction_table`(已停更) | 247 |
| **L2 feature** | `cn_equity_feature/*` 日级 + `cn_equity_feature_5min/*` 5min | 6315 + 917 |
| **行业 / 精品聚合** | `DmgrPwang_industry_equ_fancy_factors`(同事 pwang 作品) | 82 |
| **资金流** | `AShareMoneyFlow` | 95 |
| **市场指标** | `Dmgr_MktRet` / `Dmgr_adv20` / `DmgrWbai_AIndexCSI*Weight` | 4 |
| **外部研究员信号** | `signal_rsh`(**已弃用**) | 1 |

## 顶层目录:快照式视图

```
/datasvc/data/
├── cc_all/      1.6T   真实物理存储,持续日增,.meta=20260529/3995
├── cc_2024/     273G   2024 年末快照,独立物理副本,.meta=20241231/3657
├── cc_2025/     23M    2025 年末快照,文件 symlink → cc_all,.meta=20251231/3900
├── cc -> cc_2024
└── link.py             生成 cc_link/(L2 feature 精选 symlink 视图)
```

| 视图 | 物理形态 | 用途 |
|---|---|---|
| `cc_all` | 真实文件,持续日增 | **研究 / 挖掘默认入口**,能读到最新数据 |
| `cc_2025` | 文件 symlink 到 cc_all,但 `.meta` 独立 | reproduce 2025 末时点的回测,gsim 严格按 .meta 截断 |
| `cc_2024` | 独立物理副本(close.npy 160M = 3657×5484×8) | reproduce 2024 末时点,与之后写入完全隔离 |
| `cc` | 软链到 cc_2024 | 默认稳定快照 |

### `.meta` 的角色

每个数据目录下的 `.meta` 不是过时的描述,而是 **gsim 的硬约束 / 快照锁**:

```
20251231         <- lastDate
dateCapacity 3900
instrumentCapacity 5484
```

- gsim 读 memmap 时严格按 `.meta.lastDate` / `dateCapacity` 截断,**保证某个时点快照不会偷未来 / 不会用未审计数据**
- 同一份物理文件,搭配不同 `.meta`,就能给出不同时点的"视图"(cc_2025 就是这么做的)
- **在 gsim 框架内一律以 `.meta` 为准**;**在数据探索 / ML 训练时**,可以扫尾部实际非 NaN 行,用满最新数据

## 全局坐标(以 cc_all 为基准)

存储格式:gsim 自定义二进制,**无 numpy header**,用 `np.memmap` 按形状直读。

| 项 | 值 |
|---|---|
| `__universe/Dates.npy` | int64 YYYYMMDD,**3995 天**,20091214 → 20260529 |
| `__universe/Instruments.npy` | U32 unicode(6 字符代码),**5484 只**,如 `000001` → `688796` |
| 日级矩阵 | `(T, N)` float64(行情/因子) 或 int8(mask) |
| 5min 矩阵 | `(T, 49, N)` float64(48 个 5min bar + 1 个收盘集合竞价) |
| 一致预期 | `(T, 3, N)` 年度(FY0/FY1/FY2) 或 `(T, 12, N)` 季度(未来 12 个季度) |
| 早期未上市 | float64 NaN(`0x7ff8000000000000`),int8 0 |

## 各数据组真实填充进度(cc_all 实测)

通过扫每个 memmap 尾部找最后一个非 NaN 行得到。

| 数据类 | T_cap | 真实 last_idx | 最后日期 | 增长 |
|---|---|---|---|---|
| Basedata / ashare* / equ_factor_* / AShareMoneyFlow / Interval5m / 行情 / universe | 3995 | 3994 | 20260529 | 日增,基准 |
| `equ_fancy_factors_table*`(精品因子) | 3995 | 3994 | 20260529 | 日增 |
| `*_fore_annual` / `*_fore_quarter`(AI 盈利预测) | 3995 | 3994 | 20260529 | 日增 |
| `ashareconsensusrollingdata_*`(分析师预测) | 3995 | 3994 | 20260529 | 日增 |
| `cn_equity_feature/*` (6315 维日级 L2) | 4636 | 3994 | 20260529 | 日增,与 Basedata 同步 |
| `cn_equity_feature_5min/*` (917 维 5min L2) | 4636 | ~3958 | ~20260403 | 日增,~30 个交易日 lag |
| `realtime/feature_cuts_1430/*` (32 维) | 4636 | ~3959 | ~20260407 | 同 5min 节奏 |
| `cn_equity/forward_returns/*` (64 标签) | 4636 | ~3869 | ~20251119 | 标签需未来收益,自带物理 lag |
| `equ_h2l_factor_t1~t4`(h2l 因子) | 3995 | 冻结 | — | **无增量**(rawdata_datayes_unused) |
| `hf_daily_*`(高频衍生表) | 3995 | 冻结 | — | **无增量**(rawdata_datayes_unused) |
| `signal_rsh` | 3900 | 3900 | 20251231 | **已弃用** |
| `delta/daily/YYYYMMDD/*` | 切片式 (F, N) | 3635 → 3994 (360 天) | 20260529 | 日落盘,F 维度自增(2531 → 3214) |

L2-feature 使用 NioData memmap **预分配到 4636 行**,日增直接 in-place 追加,不需要 resize 文件。

## 数据分组(语义视角)

### 1) Universe / 状态掩码 — (T, N) int8

| 目录 | 含义 |
|---|---|
| `ALL` / `ALL_GIM` / `ALL_TRD` / `FULL` | 全市场不同口径(上市可交易 / 已 IPO / 上市未停牌) |
| `HS300` / `ZZ500` / `ZZ1000` | 指数成分股 |
| `TOP1000/1500/2000/2600/3000/3300/4000` | 按市值 / 流动性 top-K 池 |
| `Basedata/{st,status}.npy` | ST / 停牌状态 |
| `ipo/ipodate.npy` | (N,) IPO 日期 |
| `PriceLimit/{upper,lower}_limit.npy` | (T, N) 涨跌停价 |

**挖掘默认 mask**: `TOP3000 & ALL_TRD & ~st & ~status`(过滤涨跌停看 PriceLimit)。

### 2) 行情 / 基础 — (T, N) float64

| 目录 | #字段 | 来源 | 关键内容 |
|---|---|---|---|
| `Basedata` | 18 | dm 层(基于 wind) | open/high/low/close/vwap/volume/amount/cap/capfree/tradecnt/industry/sector/subsector/exchange/country/st/status |
| `ashareeodprices` | 20 | wind | Wind 风格行情,含复权(s_dq_adjclose/adjfactor/preclose/pctchange/limit/stopping) |
| `adjfactor` | 1 | wind | 复权因子 |
| `PriceLimit` | 2 | wind | upper / lower limit |
| `aindexeodprices` | 81 | wind | 指数行情(000001/000300/000852/000016 等),`(T,)` 一维 |
| `Interval5m` | 9×49 | **citics** | 5min bar: open/high/low/close/vwap/volume/amount/tradecnt/... × 49 bar |

### 3) 量价衍生因子 — (T, N) float64,dm 层

| 目录 | #字段 | 含义 |
|---|---|---|
| `Dpv`, `Dpva`, `Dpvb`, `Dpvc`, `Dpvd` | 各 20 | 量价合成因子族(不同窗口 / 算法) |
| `Dipv`, `Dipva` | 各 20 | 增量版本 |
| `Dmgr_adv20` | 1 | 20 日平均成交额 |
| `Dmgr_MktRet` | 1 | 市场平均收益 |
| `DmgrWbai_AIndexCSI500/1000Weight` | 各 1 | 指数权重 |

### 4) 资金流 — (T, N) float64,wind 源

`AShareMoneyFlow` 共 95 个字段,按"订单档位 × 买卖方向 × 计数维度"分桶:

- 档位: exlarge / large / med / small
- 方向: buy / sell / net
- 维度: trades / value / volume

### 5) 高频日级衍生(hf_daily_*) — (T, N) float64,datayes 弃用源 — **无增量**

10 个表共 **247 个字段**,按主题分。注意:这些数据来自 `rawdata_datayes_unused/`,**已停更**,只能用历史段做研究:

| 目录 | #字段 | 主题 |
|---|---|---|
| `hf_daily_auction_table` | 24 | 集合竞价(DOWN_LIMIT / JUMP_RET / LAST_HALF_MINUTE_RET) |
| `hf_daily_der_table_1` | 17 | 价格压力(DP_NEG / DP_POS 相关) |
| `hf_daily_der_table_2` | 16 | 流动性弹性(BUY_ILLIQ / BUY_LAMBDA / LIQ_ELAS) |
| `hf_daily_der_table_3` | 27 | 主动买盘(ACTIVE_BUY_*) |
| `hf_daily_der_table_4` | 16 | 日内动量(INTRADAY_MOMENTUM*) |
| `hf_daily_der_table_5` | 17 | 当日盘口(APB_1D / ARPP_1D / BUYSELL_SHEET) |
| `hf_daily_der_table_6` | 26 | 振幅 / 方向(AMP_VOL / UP_DOWN_VOL_RATIO) |
| `hf_daily_der_table_7` | 34 | 行为指标(ACT / BCVP*) |
| `hf_daily_der_table_8` | 35 | 撤单 / 试单(AUCTION_CANCEL / BUY_T_OR_*) |
| `hf_daily_der_table_9` | 35 | 大单买卖(AFH_* / BB_SB_* / BIG_BUY_RATIO) |

### 6) 5min 高频特征(L2 衍生) — (T, 49, N) float64,L2-feature 层

`cn_equity_feature_5min` 共 **917 个特征**,3 个子集,基于 L2 行情 build:

| 子集 | #字段 | 内容 |
|---|---|---|
| `yq_212_5min` | 410 | 订单 / 成交 5min 序列(order_sell_count 等) |
| `fb_224_5min` | 450 | trd_vwap_5m 等 |
| `dw_57_5min` | 57 | 五档 bid / ask |

注意:单文件逻辑大小 ~10G(`T_cap × 49 × N × 8`),但物理为稀疏 memmap,早期 NaN 不占盘。**别一次性全部加载**,按需切片。

### 7) 424 因子(gsim 风格日级 alpha) — (T, N) float64,datayes 源

`equ_factor_*` 共 **15 表 / 433 字段**,公司内部俗称 **"424 因子"**:

| 表 | 含义 | #字段 |
|---|---|---|
| `equ_factor_obos` | 超买超卖(ADTM/ATR/BIAS) | 62 |
| `equ_factor_trend` | 趋势(AD/EMA/MACD) | 39 |
| `equ_factor_return` | Alpha20/60/120 + Beta + GainVariance + CmraCNE5(BARRA 风格) | 35 |
| `equ_factor_volume` | 量能(DAVOL) | 34 |
| `equ_factor_derive` | 财务衍生(TTM) | 43 |
| `equ_factor_pq` | 估值 / 质量 | 37 |
| `equ_factor_psi` | 每股指标 | 24 |
| `equ_factor_vs` | 估值 / 市值 | 28 |
| `equ_factor_sc` | 资本结构 | 27 |
| `equ_factor_ma` | 均线 | 20 |
| `equ_factor_power` | 动能 | 18 |
| `equ_factor_cf` | 现金流 | 17 |
| `equ_factor_growth` | 成长 | 15 |
| `equ_factor_af` | 一致预期 | 12 |
| `equ_factor_oc` | 经营周期 | 12 |

### 8) 精品因子 / h2l 因子 / 行业聚合

| 目录 | #字段 | 来源 / 增量 | 内部叫法 |
|---|---|---|---|
| `equ_fancy_factors_table1~10` | 208 | datayes,正常更新 | **精品因子**(AI_*/AST_*/APB_*/FF3R2_* 学术风格) |
| `equ_h2l_factor_t1~t4` | 122 | datayes 弃用源,**无增量** | **高频转低频(h2l)因子**(买卖意图、5min 振幅、阻力价) |
| `DmgrPwang_industry_equ_fancy_factors` | 82 | dm 层(pwang 同事 build) | **行业 + 精品因子聚合**(t10 + YOY) |

### 9) 财务原始(三大报表) — (T, N) float64,wind 源

报告期已对齐到交易日的"point-in-time"数据。

| 目录 | #字段 |
|---|---|
| `asharebalancesheet` | 174(全资产负债表科目) |
| `asharecashflow` | 118(现金流量表) |
| `ashareincome` | 103(利润表) |

### 10) 分析师预测 + AI 盈利预测

**分析师预测**(`ashareconsensusrollingdata_*`,wind 源)— (T, N) f64,每个目录 17 个字段:

`ashareconsensusrollingdata_{FY0, FY1, FY2, FY3, FTTM, CAGR, YOY, YOY2}` 共 8 个口径 × 17 字段 = 136 字段。

口径含义:FY0/1/2/3 = 当前 / 未来 1~3 个会计年度;FTTM = 未来滚动 12 月;CAGR = 复合增速;YOY/YOY2 = 同比 / 环比同比。

**AI 盈利预测**(`*_fore_annual` / `*_fore_quarter`,datayes 源)— (T, K, N) f64:

| 目录 | K | #字段 |
|---|---|---|
| `balance_sheet_fore_annual` | 3(FY0/FY1/FY2) | 41 |
| `cash_flow_statement_fore_annual` | 3 | 23 |
| `finance_ratio_fore_annual` | 3 | 37 |
| `financial_summary_fore_annual` | 3 | 22 |
| `income_statement_fore_annual` | 3 | 42 |
| `revenue_forecast_annual` | 3 | 2 |
| `financial_summary_fore_quarter` | 12(未来 12 个季度) | 12 |
| `income_statement_fore_quarter` | 12 | 32 |
| `revenue_forecast_quarter` | 12 | 2 |

### 11) L2 feature — (T=4636, N) float64,L2-feature 层

`cn_equity_feature/*` 共 **6315 个特征**,**我们自己基于 L2 行情(逐笔成交 + 五档盘口)build 出来的日级 / 多频率特征**。按作者 / 批次分桶:

| 子集 | #files | 备注 |
|---|---|---|
| `fguo_max` | 1634 | 最大批 |
| `fguo_trade3` | 1202 | |
| `fguo_ywang` | 818 | |
| `fguo_trade2` | 542 | |
| `sli_0211` | 384 | |
| `sli_0212` | 282 | |
| `sli_0215` | 194 | |
| `sli_0210` | 162 | |
| `sli_0213` | 162 | |
| `fguo_1224_trade` | 138 | |
| `fguo_1230` | 114 | |
| `fguo_0106` | 158 | |
| `fguo_0105` | 108 | |
| `sli_0214` | 98 | |
| `sli_0206` | 84 | |
| `fguo_1224_order` | 72 | |
| `fguo_1208` | 60 | |
| `fguo_1209` | 54 | |
| `feature_cuts_1430` | 32 | 14:30 切片 |
| `zzk_19` | 17 | trade count 基础 |

### 12) 监督标签

`cn_equity/forward_returns/` 共 **64 个**:

`fwd_return_close_delay{0,1,2,3}_{1,2,3,4,5,6,7,8,9,10,15,20,30,40,50,60}d`

- `delay0_1d` = T+0 收盘 → T+1 收盘(可能偷未来,慎用)
- `delay1_5d` = T+1 收盘 → T+6 收盘(**挖掘默认 Y**,模拟次日开盘建仓后 5 日收益)
- `delay1_20d` 中线;`delay1_1d` 短线

### 13) 增量 / 实时

| 目录 | 形态 | 说明 |
|---|---|---|
| `delta/daily/YYYYMMDD/` | 每日一个目录 | 内含 `{date}.npy` (F, N)、`features.csv`(列出 feature_idx → name → memmap_dir → source_table)、`meta.json`(shape / dtype / date_idx) |
| `realtime/feature_cuts_1430/` | (T_cap, N) | 32 个 14:30 时点切片字段(L2-feature 层) |
| `signal_rsh` | (T, N) | 之前外部研究员合作信号,**已弃用**,只是没删除 |

`delta/daily` 是逐日 ETL 落盘的"特征日切片",特征数从 2024-12-02 的 2531 涨到 2026-05-29 的 3214。跟 cn_equity_feature 的 6315 维有交集(`features.csv` 里指明每个 feature 来源目录,可以追溯到 cn_equity / cn_equity_feature 下的某个表)。

## 读取 snippet

```python
import numpy as np

ROOT = '/datasvc/data/cc_all'   # 或 cc_2025 / cc_2024 用快照
T, N = 3995, 5484                # Basedata / ashare* 等真实形状
T_FEAT = 4636                    # cn_equity_feature / cn_equity / realtime 容量

# 元数据
dates = np.fromfile(f'{ROOT}/__universe/Dates.npy', dtype='int64')
insts = np.fromfile(f'{ROOT}/__universe/Instruments.npy', dtype='U32')

# 日级 (T, N) float64
close = np.memmap(f'{ROOT}/Basedata/close.npy',
                  dtype='f8', mode='r', shape=(T, N))
adj   = np.memmap(f'{ROOT}/ashareeodprices/ashareeodprices.s_dq_adjclose.npy',
                  dtype='f8', mode='r', shape=(T, N))

# (T, N) int8 mask
top3000  = np.memmap(f'{ROOT}/TOP3000/TOP3000.npy',
                     dtype='i1', mode='r', shape=(T, N))
all_trd  = np.memmap(f'{ROOT}/ALL_TRD/ALL_TRD.npy',
                     dtype='i1', mode='r', shape=(T, N))

# 5min 行情 (T, 49, N)
h5 = np.memmap(f'{ROOT}/Interval5m/Interval5m.high.npy',
               dtype='f8', mode='r', shape=(T, 49, N))

# L2 feature (T_FEAT, N) — 注意容量是 4636,但实际填充看真实非 NaN 末端
feat = np.memmap(f'{ROOT}/cn_equity_feature/fguo_max/fguo_max.vol_trade_h1_h4_ratio.npy',
                 dtype='f8', mode='r', shape=(T_FEAT, N))

# Y 标签
y = np.memmap(f'{ROOT}/cn_equity/forward_returns/fwd_return_close_delay1_5d.npy',
              dtype='f8', mode='r', shape=(T_FEAT, N))

# 5min L2 feature (T_FEAT, 49, N)
f5 = np.memmap(f'{ROOT}/cn_equity_feature_5min/yq_212_5min/yq_212_5min.order_sell_count.npy',
               dtype='f8', mode='r', shape=(T_FEAT, 49, N))

# delta/daily 日切片 (F, N)
import json
with open(f'{ROOT}/delta/daily/20260529/meta.json') as f:
    m = json.load(f)
day = np.memmap(f'{ROOT}/delta/daily/20260529/20260529.npy',
                dtype=m['dtype'], mode='r', shape=tuple(m['shape']))
```

### 找文件真实填充末端

```python
def last_filled_idx(arr):
    """从尾部扫,返回最后一个有任何非 NaN 的行 idx"""
    for di in range(arr.shape[0] - 1, -1, -1):
        if np.any(~np.isnan(arr[di])):
            return di
    return None
```

## 因子挖掘对齐规则

1. **训练样本期**:日级 L2 (`cn_equity_feature/*`) 跟 Basedata 同步到 idx 3994 (20260529),可直接对齐切片。
2. **5min L2 / realtime**:有 ~30 个交易日 lag,使用前先扫真实非 NaN 末端。
3. **Y 标签 (`forward_returns/*`)**:只到 ~20251119;若要更近 OOS,自己用 `ashareeodprices.s_dq_adjclose_backward × pctchange` 或 `Basedata/close × adjfactor` 补算。
4. **Universe 过滤**:训练默认 `TOP3000 & ALL_TRD & ~st`;敏感场景再叠加 `PriceLimit` 排除涨跌停。
5. **形状不一致**:Basedata 等 (3995, N),L2-feature (4636, N) —— 对齐时按 idx 切,取共同窗口。
6. **快照切换**:严格 OOS / 复盘历史回测,把 `ROOT` 改成 `cc_2024` / `cc_2025`,数据 / `.meta` 自动一致。
7. **停更数据慎用**:`equ_h2l_factor_*` / `hf_daily_*`(rawdata_datayes_unused 源)、`signal_rsh`(已弃用)只能用历史段,实盘 / OOS 看不到新数据。
8. **完整字段清单**:见 [cc_all_fields.csv](cc_all_fields.csv),3 列(top_dir / sub_dir / field)共 9901 行,grep / pandas 直接查。

## 与 gsim 框架的关系

- 在 gsim 框架内开发因子:用 `dr.getData('source.field')`,XML 注册数据源,见 [data-sources.md](data-sources.md)。
- 跳过 gsim 直接 ML / 探索:用本文档的 `np.memmap` 方式直读,**绕过 `.meta` 限制可以拿到最新数据**,但要自己保证不偷未来。
- 两者读的是同一份物理文件,数据语义完全一致。
