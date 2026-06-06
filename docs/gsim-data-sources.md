# Gsim 数据源参考

本文档列举 gsim 中可用的数据源(Dmgr 模块),面向**在 gsim 框架内开发因子**的场景。完整列表见 `/usr/local/gsim/gsim/data/module/__init__.py` 和 `/datasvc/template/config.read_cache.xml`(111 个 `<Data id>` 注册项)。

> 物理数据层(memmap 文件布局、形状、填充进度、快照机制)请看 [cc-data-layout.md](cc-data-layout.md)。本文档专注于"XML 注册 + `dr.getData()` 调用"这一层。

## 数据分层

```
rawdata  →  cc (common cache)  →  dm (data manager)  →  L2-feature
   ↓             ↓                      ↓                      ↓
原始数据源     /datasvc/data/cc_all   gsim Dmgr 模块         L2 衍生特征层
```

- **rawdata**: `/datasvc/rawdata/{rawdata_wind, rawdata_datayes, rawdata_citics, rawdata_datayes_unused}`
- **cc**: `/datasvc/data/{cc, cc_2024, cc_2025, cc_all}`,memmap 标准化 cache
- **dm**: gsim DataManager 模块(本文档的主角),用 `<Data module="Dmgr*">` 注册,基于 cc / rawdata build
- **L2-feature**: 自建 L2 特征(`cn_equity_feature*` / `realtime` / `delta`),也通过 Dmgr 注册供 gsim 使用。**不要跟 `/tank/vault/alphalib/alpha_feature/`** (`ops pack` 输出的因子矩阵) 混淆。

gsim 的 XML `<Data>` 注册项实际上在加载某个 dm 模块,该模块再读 rawdata / cc 下的物理文件。

## 内部命名 ↔ gsim Module 对照

| 内部叫法 | XML id 前缀 | Dmgr Module | 增量 |
|---|---|---|---|
| **424 因子** | `equ_factor_*`(15 个) | `Dmgrequ_factor_*` | ✅ datayes |
| **精品因子** | `equ_fancy_factors_table1~10` | `Dmgr_equ_fancy_factors_tableN` | ✅ datayes |
| **AI 盈利预测** | `*_fore_annual` / `*_fore_quarter` | `DmgrWbai_AIFcst_*` / `source_ref/` | ✅ datayes |
| **分析师预测** | `ashareconsensusrollingdata_*`(8 口径) | `Dmgr_consensusexpectationfactor.py` 等 | ✅ wind |
| **h2l 高频转低频因子** | `equ_h2l_factor_t1~t4` | `source_ref/Dmgr_equ_h2l_factor_t*.py` | ❌ **停更**(rawdata_datayes_unused) |
| **高频衍生表** | `hf_daily_der_table_1~9` + `hf_daily_auction_table` | `source_ref/Dmgr_hf_daily_*.py` | ❌ **停更** |
| **L2 feature** | `cn_equity_feature/*` / `cn_equity_feature_5min/*` | `dm_src/dmgr_*.py`(yq_212 / fb_224 / fguo / sli / zzk 等) | ✅ 自研 |
| **行业聚合** | `DmgrPwang_industry_equ_fancy_factors` | dm_src 下,pwang 同事作品 | ✅ |
| **资金流** | `AShareMoneyFlow` | `DmgrAShareMoneyFlow` | ✅ wind |
| **5min 行情** | `Interval5m` | `source_ref/interval_5m_zx.py` | ✅ citics |
| **市场指标** | `Dmgr_MktRet` / `Dmgr_adv20` / `DmgrWbai_AIndexCSI*Weight` | dm_src / source_ref | ✅ |
| **外部研究员信号** | `signal_rsh` | — | ❌ **已弃用** |

## 调用范式

所有数据通过 `dr.getData('source.field')` 访问：

```python
from gsim import DataRegistry as dr

# 二维矩阵（日频）
self.s_dq_close = dr.getData('ashareeodprices.s_dq_close')

# 三维立方（分钟频）
self.close_m5 = dr.getData('Interval5m.close')

# 部分数据需要 .data 属性
self.vol = dr.getData('volume').data
```

使用前必须在 XML 的 `<Modules>` 中注册对应的 `<Data id="..." module="..."/>`。

## 标的池（Universe）模块

控制回测的股票范围。

| ID | Module | 说明 |
|----|--------|------|
| `ALL` | `UmgrAll` | 全市场标的 |
| `ALL_TRD` | `UmgrTrd` | 可交易标的（最常用） |
| `FULL` | `/usr/local/gsim/gsim/data/module/umgr_full.py` | 全集（含退市） |
| `ALL_GIM` | `/usr/local/gsim/gsim/data/module/umgr_gim.py` | GIM 标的池 |
| `HS300` | `/usr/local/gsim/source_ref/umgr_index.py` | 沪深 300 |
| `ZZ500` | `/usr/local/gsim/source_ref/umgr_index.py` | 中证 500 |
| `ZZ1000` | `/usr/local/gsim/source_ref/umgr_index.py` | 中证 1000 |
| `TOP1000` ~ `TOP4000` | `/usr/local/gsim/source_ref/umgr_topliquid.py` | 按流动性 TOP N（1000/1500/2000/2600/3000/3300/4000） |

注册示例（指数标的池需要 `dataPath`）：

```xml
<Data id="ALL_TRD" module="UmgrTrd" path="" niomapprivate="true"/>
<Data id="HS300" module="/usr/local/gsim/source_ref/umgr_index.py"
    dataPath="/datasvc/rawdata/rawdata_wind/HS300/" niomapprivate="true"/>
<Data id="TOP3000" module="/usr/local/gsim/source_ref/umgr_topliquid.py"
    univsize="3000" niomapprivate="true"/>
```

## 基础数据

| ID | Module | 主要字段 | 说明 |
|----|--------|---------|------|
| `Basedata` | `DmgrBasedata`（或生产用 `/usr/local/gsim/source_ref/base_data_2026.py`） | `volume`, `cap`, `industry`, `sector`, `st`, `status` | 综合基础数据 |
| `ipo` | `DmgrIPO` | - | IPO 日期/状态 |
| `PriceLimit` | `DmgrPriceLimit` | - | 涨跌停限制 |
| `adjfactor` | `DmgrAdjfactor` | - | 复权因子 |
| `adjprice` | `DmgrAdjprice` | - | 复权价格 |

注册示例：

```xml
<Data id="Basedata" module="DmgrBasedata"
    rawpricePath="/datasvc/rawdata/rawdata_wind/rawprice"
    industryPath="/datasvc/rawdata/rawdata_wind/AShareWindIndustry/"
    ST="/datasvc/rawdata/rawdata_wind/AShareST"
    niomapprivate="true"/>
<Data id="ipo" module="DmgrIPO" dataPath="/datasvc/rawdata/secID"/>
<Data id="PriceLimit" module="DmgrPriceLimit"
    dataPath="/datasvc/rawdata/rawdata_wind/pricelimit"/>
<Data id="adjfactor" module="DmgrAdjfactor"
    dataPath="/datasvc/rawdata/rawdata_wind/adjfactor"/>
<Data id="adjprice" module="DmgrAdjprice"/>
```

调用示例：

```python
self.vol = dr.getData('volume').data       # 来自 Basedata
self.cap = dr.getData('cap')               # 来自 Basedata
self.sector = dr.getData('sector')         # 行业分类
```

## 行情数据

### A 股日线（Dmgrashareeodprices）

| 字段 | 说明 |
|-----|------|
| `s_dq_open` | 开盘价 |
| `s_dq_high` | 最高价 |
| `s_dq_low` | 最低价 |
| `s_dq_close` | 收盘价 |
| `s_dq_volume` | 成交量 |
| `s_dq_amount` | 成交额 |
| `s_dq_pctchange` | 涨跌幅 |
| `s_dq_avgprice` | 均价 |

注册：

```xml
<Data id="ashareeodprices" module="Dmgrashareeodprices"
    dataPath="/datasvc/rawdata/rawdata_wind/ashareeodprices/"
    niomapprivate="true"/>
```

调用：

```python
self.close = dr.getData('ashareeodprices.s_dq_close')
self.ret = dr.getData('ashareeodprices.s_dq_pctchange')
```

### 指数日线（Dmgraindexeodprices）

| 字段 | 说明 |
|-----|------|
| `s_dq_close_000300` | 沪深 300 收盘价 |
| `s_dq_pctchange_000300` | 沪深 300 涨跌幅 |
| `s_dq_close_000905` | 中证 500 收盘价 |
| `s_dq_pctchange_000905` | 中证 500 涨跌幅 |
| `s_dq_close_000852` | 中证 1000 收盘价 |
| `s_dq_pctchange_000852` | 中证 1000 涨跌幅 |

注册：

```xml
<Data id="aindexeodprices" module="Dmgraindexeodprices"
    dataPath="/datasvc/rawdata/rawdata_wind/aindexeodprices"
    niomapprivate="true"/>
```

调用：

```python
self.idx = dr.getData('aindexeodprices.s_dq_pctchange_000905')
```

### 5 分钟 K 线（DmgrInterval5m）

3D 立方数据，形状 `(n_dates, n_bars, n_stocks)`。

| 字段 | 说明 |
|-----|------|
| `open` | 开盘价 |
| `high` | 最高价 |
| `low` | 最低价 |
| `close` | 收盘价 |
| `volume` | 成交量 |
| `amount` | 成交额 |

`ti` 索引：
- `0`: 集合竞价
- `1` ~ `48`: 9:30 后每 5 分钟（13:00 跳过午休）

注册：

```xml
<Data id="Interval5m" module="/usr/local/gsim/source_ref/interval_5m_zx.py"
    dataPath="/datasvc/rawdata/rawdata_citics/Interval5m/"
    niomapprivate="true"/>
```

调用：

```python
self.close_m5 = dr.getData('Interval5m.close')
# 14:30 收盘价
bar_44 = self.close_m5[di - self.delay, 44, valid_idx]
```

### 资金流（DmgrAShareMoneyFlow）

| 字段（部分） | 说明 |
|-----|------|
| `net_inflow_rate_volume` | 净流入量比率 |
| `buy_value_exlarge_order` | 超大单买入额 |
| `sell_value_exlarge_order` | 超大单卖出额 |
| ... | 更多字段见源码 |

调用：

```python
self.flow = dr.getData('AShareMoneyFlow.net_inflow_rate_volume')
```

## 财务数据

| ID | Module | 说明 |
|----|--------|------|
| `asharebalancesheet` | `Dmgrasharebalancesheet` | 资产负债表 |
| `ashareincome` | `Dmgrashareincome` | 利润表 |
| `asharecashflow` | `Dmgrasharecashflow` | 现金流量表 |

字段众多，参考 Wind 财务字段命名（如 `accounts_payable`、`net_profit_excl_min_int_inc`、`operate_cash_flow` 等）。

调用：

```python
self.ap = dr.getData('asharebalancesheet.accounts_payable')
self.np = dr.getData('ashareincome.net_profit_excl_min_int_inc')
self.ocf = dr.getData('asharecashflow.operate_cash_flow')
```

## 分析师预测(ashareconsensusrollingdata 系列)

公司内部俗称 **"分析师预测"**,Wind 一致预期数据,按口径分 8 个模块,每个模块 17 字段,共 136 字段:

| ID | 口径 |
|----|------|
| `ashareconsensusrollingdata_CAGR` | 复合增长率 |
| `ashareconsensusrollingdata_FTTM` | 滚动 TTM |
| `ashareconsensusrollingdata_FY0` | 当年预测 |
| `ashareconsensusrollingdata_FY1` | 下一年预测 |
| `ashareconsensusrollingdata_FY2` | 下两年预测 |
| `ashareconsensusrollingdata_FY3` | 下三年预测 |
| `ashareconsensusrollingdata_YOY` | 同比 |
| `ashareconsensusrollingdata_YOY2` | 两年同比 |

常用字段:`est_eps`, `est_pe`, `est_roe`, `est_oper_rev`, `est_oper_profit` 等。

调用:

```python
self.eps_fy1 = dr.getData('ashareconsensusrollingdata_FY1.est_eps')
```

## 424 因子(equ_factor 系列)

公司内部俗称 **"424 因子"**,DataYes 因子库,按类别分 15 个模块,共 433 个字段:

| ID | 类别 |
|----|------|
| `equ_factor_oc` | OC(Order Capacity / 经营周期) |
| `equ_factor_growth` | 成长 |
| `equ_factor_power` | 动量 |
| `equ_factor_cf` | 现金流 |
| `equ_factor_psi` | PSI(每股指标) |
| `equ_factor_sc` | SC(资本结构) |
| `equ_factor_vs` | VS(估值 / 市值) |
| `equ_factor_return` | 回报(BARRA 风格 Alpha/Beta/Gain/Cmra) |
| `equ_factor_volume` | 量能(DAVOL) |
| `equ_factor_trend` | 趋势(AD/EMA/MACD) |
| `equ_factor_pq` | PQ(估值 / 质量) |
| `equ_factor_derive` | 财务衍生(TTM) |
| `equ_factor_obos` | 超买超卖(ADTM/ATR/BIAS) |
| `equ_factor_ma` | 均线 |
| `equ_factor_af` | AF(一致预期) |

注册示例:

```xml
<Data id="equ_factor_return" module="Dmgrequ_factor_return"
    dataPath="/datasvc/rawdata/rawdata_datayes/equ_factor_return"
    niomapprivate="true"/>
```

## 精品因子(equ_fancy_factors)

公司内部俗称 **"精品因子"**,DataYes 高阶因子表,共 10 个表 / 208 字段。gsim 内置 1-8,9-10 通过自定义模块路径注册:

| ID | 范围 |
|----|------|
| `equ_fancy_factors_table1` ~ `table8` | gsim 内置 |
| `equ_fancy_factors_table9` ~ `table10` | 通过自定义模块路径注册 |

注册:

```xml
<Data id="equ_fancy_factors_table1" 
    module="/usr/local/gsim/source_ref/Dmgr_equ_fancy_factors_table1.py"
    dataPath="/datasvc/rawdata/rawdata_datayes/equ_fancy_factors_table1"
    niomapprivate="true"/>
```

## 行业 + 精品因子聚合(DmgrPwang)

`DmgrPwang_industry_equ_fancy_factors`(82 字段),pwang 同事 build,基于 dm 层做的行业聚合 + 精品因子组合(包含 t10 + YOY 衍生)。属于 dm 层产物。

## h2l 高频转低频因子(equ_h2l_factor) — 已停更

**rawdata_datayes_unused 来源,无增量**,只能用历史段做研究,实盘 / OOS 看不到新数据。

| ID | 字段数 |
|----|--------|
| `equ_h2l_factor_t1` | 31 |
| `equ_h2l_factor_t2` | 31 |
| `equ_h2l_factor_t3` | 30 |
| `equ_h2l_factor_t4` | 30 |

字段:买卖意图、5min 振幅、阻力价、APT_INFLOW_RATIO 等。

位于 `/usr/local/gsim/source_ref/`,需通过完整路径注册。

## 高频衍生表(hf_daily_*) — 已停更

**rawdata_datayes_unused 来源,无增量**。10 个表 / 247 个字段:

| ID | 主题 |
|----|------|
| `hf_daily_auction_table` | 集合竞价 |
| `hf_daily_der_table_1` | 价格压力 |
| `hf_daily_der_table_2` | 流动性弹性 |
| `hf_daily_der_table_3` | 主动买盘 |
| `hf_daily_der_table_4` | 日内动量 |
| `hf_daily_der_table_5` | 当日盘口 |
| `hf_daily_der_table_6` | 振幅 / 方向 |
| `hf_daily_der_table_7` | 行为指标 |
| `hf_daily_der_table_8` | 撤单 / 试单 |
| `hf_daily_der_table_9` | 大单买卖 |

## 自定义 DPV 系列

`gsim/data/module/` 提供的自定义数据：

| Module | 说明 |
|--------|------|
| `DmgrDpv` | DPV 基础 |
| `DmgrDpva` | DPV-A |
| `DmgrDpvb` | DPV-B |
| `DmgrDpvc` | DPV-C |
| `DmgrDpvd` | DPV-D |
| `DmgrDipv` | DiPV 基础 |
| `DmgrDipva` | DiPV-A |

源码位于 `/usr/local/gsim/dm_src/dmgr_dpv*.py`。

注册：

```xml
<Data id="Dpv" module="/usr/local/gsim/dm_src/dmgr_dpv.py"
    dataPath="/usr/local/gsim/dm_src/dmgr_dpv.py" niomapprivate="true"/>
```

## AI 盈利预测(*_fore_annual / *_fore_quarter)

公司内部俗称 **"AI 盈利预测"**,DataYes 预测财报,需要在 XML 中通过完整路径注册,源码在 `source_ref/`。`(T, K, N)` 三维 — K=3 (FY0/FY1/FY2) 或 K=12 (未来 12 个季度):

| ID | K | 字段数 |
|----|---|--------|
| `balance_sheet_fore_annual` | 3 | 41 |
| `cash_flow_statement_fore_annual` | 3 | 23 |
| `finance_ratio_fore_annual` | 3 | 37 |
| `financial_summary_fore_annual` | 3 | 22 |
| `income_statement_fore_annual` | 3 | 42 |
| `revenue_forecast_annual` | 3 | 2 |
| `financial_summary_fore_quarter` | 12 | 12 |
| `income_statement_fore_quarter` | 12 | 32 |
| `revenue_forecast_quarter` | 12 | 2 |

注册示例:

```xml
<Data id="revenue_forecast_annual"
    module="/usr/local/gsim/source_ref/DmgrWbai_AIFcst_revenue_forecast_annual.py"
    dataPath="/datasvc/rawdata/rawdata_datayes/revenue_forecast_annual/"
    niomapprivate="true"/>
```

调用:

```python
self.rev_fa = dr.getData('revenue_forecast_annual.revenue')
```

## 指数权重

| ID | 说明 |
|----|------|
| `DmgrWbai_AIndexCSI500Weight` | 中证 500 权重 |
| `DmgrWbai_AIndexCSI1000Weight` | 中证 1000 权重 |

## L2 自建特征(cn_equity_feature 系列)

我们自己基于 L2 行情(逐笔成交 + 五档盘口)build 的多频率特征,属于 L2-feature 层。`/usr/local/gsim/dm_src/` 下提供对应 Dmgr,默认在 `config.read_cache.xml` 中被注释,**按需取消注释**。

| ID | 说明 |
|----|------|
| `yq_212_5min` | 宇其 L2 5min 频(410 字段) |
| `fb_224_5min` | FB L2 5min(450 字段) |
| `dw_57_5min` | dw L2 5min 五档(57 字段) |
| `fguo_*` | fguo 系列日级特征(max/trade2/trade3/ywang/0105/0106/1208/1209/1224/1230 等,共 4844 字段) |
| `sli_021*` | sli 系列日级特征(0206/0210~15,1366 字段) |
| `zzk_19` | zzk 系列 trade count(17 字段) |
| `feature_cuts_1430` | 14:30 切片(32 字段) |

物理布局参考 [cc-data-layout.md § 11) L2 feature](cc-data-layout.md)。

## 其他 dm 层模块

| ID | 说明 |
|----|------|
| `Dmgr_MarketStats` | 市场统计 |
| `Dmgr_adv20` | 20 日均量(`Dmgr_advN.py`,需传 `ndays`) |
| `Dmgr_MktRet` | 市场收益 |
| `Dmgr_gfv2aa` / `Dmgr_L2ZZK` / `DmgrSli_021*` / `gfl2_5m` | 其它高频 / L2 特征(`dm_src/`) |
| `DmgrPwang_industry_equ_fancy_factors` | 行业 + 精品因子聚合(pwang 同事) |

需要时取消注释并按需配置 `dataPath`。

## 注册建议

1. **按需注册**: XML 中只注册因子实际使用的 `<Data>`，避免冗余
2. **路径形式**: 优先使用简短类名（`UmgrTrd`），需要自定义路径时用完整路径
3. **检查可用**: 不在 `__init__.py` 的模块需要确认 `source_ref/` 或 `dm_src/` 下存在
4. **niomapprivate**: 一般设 `true`，避免数据竞争

## 字段查找方法

由于大量 Dmgr 是 `.so` 编译模块，字段名通常通过以下方式查找：

1. **看源代码**: `source_ref/` 下保留了 Python 源码
2. **看其他因子**: `alpha_src/` 下的示例
3. **看模板**: `/datasvc/template/AlphaWbaiReversal/AlphaWbaiReversal.py`
4. **看 ops 解析**: `ops/services/info/` 实现了 `dr.getData()` 调用解析
5. **问 @白文博**

## 参考资料

- 物理数据布局(memmap、形状、快照):[cc-data-layout.md](cc-data-layout.md)
- Gsim 架构:[gsim-architecture.md](gsim-architecture.md)
- XML 配置:[gsim-xml-config.md](gsim-xml-config.md)
- 因子开发流程:[gsim-factor-workflow.md](gsim-factor-workflow.md)
- 完整数据源配置:`/datasvc/template/config.read_cache.xml`(111 个 `<Data>` 注册项)
- gsim 数据模块源码:`/usr/local/gsim/gsim/data/module/`
- 自定义数据模块:`/usr/local/gsim/dm_src/`、`/usr/local/gsim/source_ref/`
- 完整字段清单(物理层):[cc_all_fields.csv](cc_all_fields.csv)
