# Gsim XML 配置文件说明

Gsim 使用 XML 配置文件驱动回测，schema 定义在 `/usr/local/gsim/gsim/gsim.xsd`。本文档说明配置的结构、参数和高级特性。

## 顶层结构

```xml
<gsim>
    <Macros .../>          <!-- 可选：宏定义 -->
    <Constants .../>       <!-- 必填：全局常量 -->
    <Universe .../>        <!-- 必填：标的池配置 -->
    <Modules>              <!-- 可选：模块注册（子元素顺序由 XSD 强制） -->
        <Data .../>
        <Alpha .../>
        <Operation .../>
        <Stats .../>
        <Combo .../>
    </Modules>
    <Portfolio .../>       <!-- 可选：投资组合 -->
</gsim>
```

`<Modules>` 内子元素的顺序由 `gsim.xsd` 的 `<xs:sequence>` 强制：`Data → Alpha → Operation → Stats → Combo`。lxml 启用 schema 校验后顺序不符会失败。

实际可运行的最小示例参考 `/datasvc/template/AlphaWbaiReversal/Config.Wbai.Reversal.xml`；完整数据源注册示例参考 `/datasvc/template/config.read_cache.xml`。

## Constants

全局常量配置。Schema 要求 `backdays` 和 `niodatapath` 必填。

```xml
<Constants backdays="256" niodatapath="/datasvc/data/cc" niomapprivate="true"
    authorWeight="wbai:1.0,"
    time_intensive="false"
    checkpointDir="checkpoint" checkpointDays="5"/>
```

| 参数 | 必填 | 说明 |
|-----|-----|------|
| `backdays` | 是 | 回看天数（如 256） |
| `niodatapath` | 是 | 数据缓存路径（如 `/datasvc/data/cc`） |
| `niomapprivate` | 否 | 是否使用私有映射（默认 true） |
| `authorWeight` | 否 | 作者权重（格式：`author:weight,`） |
| `time_intensive` | 否 | 是否时间密集型（默认 false） |
| `checkpointDir` | 否 | checkpoint 目录（用于 `run_cp.py`） |
| `checkpointDays` | 否 | checkpoint 间隔天数（默认 5，用于 `run_cp.py`） |

## Universe

标的池配置。XSD 中 `UniverseType` 唯一显式属性是 `src`（可选 Config 引用），其它属性走 `<xs:anyAttribute processContents="lax"/>`，由 `Universe.build()` 内部读取。

```xml
<Universe startdate="20150101" enddate="20241231"
    secID="/datasvc/rawdata/secID"
    holidaysfile="/datasvc/rawdata/holidays"
    calendarfile="/datasvc/rawdata/wind_calendar.csv"/>
```

| 参数 | 说明 |
|-----|------|
| `startdate` | 回测开始日期（YYYYMMDD） |
| `enddate` | 回测结束日期（YYYYMMDD） |
| `secID` | 证券 ID 文件路径 |
| `holidaysfile` | 节假日文件路径 |
| `calendarfile` | 交易日历文件路径 |
| `src` | 可选：外部 Config 引用 |

## Modules

模块注册区，支持 5 种子元素，**顺序由 XSD 强制**：`<Data>` → `<Alpha>` → `<Operation>` → `<Stats>` → `<Combo>`。每个元素必填 `id`（唯一标识）和 `module`（模块路径或类名）。

### Data 模块

注册数据源。模块名可以是：
- 简短类名（如 `UmgrTrd`、`Dmgrashareeodprices`）—— 从 `gsim/data/module/__init__.py` 导入
- 完整路径（如 `/usr/local/gsim/source_ref/Dmgr_aindexeodprices.py`）—— 加载自定义实现

常用注册示例（来自 `/datasvc/template/config.read_cache.xml`）：

```xml
<!-- Universe -->
<Data id="ALL" module="UmgrAll" path="" niomapprivate="true"/>
<Data id="ALL_TRD" module="UmgrTrd" path="" niomapprivate="true"/>
<Data id="HS300" module="/usr/local/gsim/source_ref/umgr_index.py"
    dataPath="/datasvc/rawdata/rawdata_wind/HS300/" niomapprivate="true"/>
<Data id="TOP3000" module="/usr/local/gsim/source_ref/umgr_topliquid.py"
    univsize="3000" niomapprivate="true"/>

<!-- 基础数据 -->
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

<!-- 行情 -->
<Data id="ashareeodprices" module="Dmgrashareeodprices"
    dataPath="/datasvc/rawdata/rawdata_wind/ashareeodprices/"/>
<Data id="aindexeodprices" module="Dmgraindexeodprices"
    dataPath="/datasvc/rawdata/rawdata_wind/aindexeodprices"/>
<Data id="Interval5m" module="/usr/local/gsim/source_ref/interval_5m_zx.py"
    dataPath="/datasvc/rawdata/rawdata_citics/Interval5m/"/>
```

完整数据源参考 [gsim-data-sources.md](gsim-data-sources.md)。

### Alpha 模块（注册）

在 `<Modules>` 内的 `<Alpha>` 用于注册因子类：

```xml
<Alpha id="AlphaWbaiReversalMod"
    module="/datasvc/template/AlphaWbaiReversal/AlphaWbaiReversal.py"/>
```

注册的 `id` 在 `<Portfolio>` 内被引用。

### Combo 模块（注册）

```xml
<Combo id="Combo_bj202"
    module="/usr/local/gsim/combo_src/Combo_bj202.cpython-310-x86_64-linux-gnu.so"/>
<Combo id="Combo_sz102"
    module="/usr/local/gsim/combo_src/Combo_sz102.cpython-310-x86_64-linux-gnu.so"
    alphaDir="alpha_sample/"/>
```

`alphaDir` 是 combo 模块自定义的用户属性（XSD 用 `anyAttribute` 放行），指定 alpha 加载目录。`combo_src/` 下生产用 combo 实际均为 `.so` 编译模块。

## Portfolio

投资组合配置。在 `<Portfolio>` 内定义 `<Stats>`（统计）和 `<Alpha>`/`<Alphas>`（因子实例）。

```xml
<Portfolio id="MyPort" booksize="20e6" homecurrency="CNY">
    <Stats .../>
    <Alpha .../>
</Portfolio>
```

| 参数 | 说明 |
|-----|------|
| `id` | 组合标识 |
| `booksize` | 资金规模（如 `20e6`） |
| `homecurrency` | 货币（如 `CNY`） |

## Stats（统计配置）

不同 Stats 模块的参数略有差异。最常用的 `StatsSimpleV5`：

```xml
<Stats module="StatsSimpleV5" mode="0"
    tradePrice="close" tax="0." fee="0." slippage="0."
    printStats="true" dumpPnl="true" pnlDir="./pnl/"/>
```

| 参数 | 说明 |
|-----|------|
| `module` | 统计模块名 |
| `mode` | 回测模式（StatsSimpleV5 特有：0/1/2/3） |
| `tradePrice` | 交易价格（`close`/`open`/`vwap`） |
| `tax` | 税费 |
| `fee` | 手续费 |
| `slippage` | 滑点 |
| `printStats` | 是否打印统计 |
| `dumpPnl` | 是否生成 PNL 文件 |
| `pnlDir` | PNL 输出目录（建议绝对路径） |
| `index_ret` | 基准指数收益率（mode=1/2 需要） |
| `thres` | 分层阈值（mode=2 需要，如 `90.0` 表示 top 10%） |

### Stats 模块列表

完整 12 个 Stats 模块见 [gsim-architecture.md](gsim-architecture.md#stats-模块)，常用配置：

**默认基础统计**（来自 `/datasvc/template/config.read_cache.xml`）:
```xml
<Stats module="StatsSimple"
    tradePrice="close" tax="0." fee="0." slippage="0."
    printStats="true" dumpPnl="true" pnlDir="./pnl/"/>
```

**多空回测（StatsSimpleV5 mode=0）**:
```xml
<Stats module="StatsSimpleV5" mode="0"
    tradePrice="close" tax="0." fee="0." slippage="0."
    printStats="true" dumpPnl="true" pnlDir="./pnl/"/>
```

**指数增强（mode=1）**:
```xml
<Stats module="StatsSimpleV5" mode="1"
    index_ret="aindexeodprices.s_dq_pctchange_000905"
    tradePrice="close" tax="0." fee="0." slippage="0."
    printStats="true" dumpPnl="true" pnlDir="./pnl/"/>
```

**分层统计（mode=2，top 10%）**:
```xml
<Stats module="StatsSimpleV5" mode="2" thres="90.0"
    index_ret="aindexeodprices.s_dq_pctchange_000905"
    tradePrice="close" tax="0." fee="0." slippage="0."
    printStats="true" dumpPnl="true" pnlDir="./pnl/"/>
```

**纯多头（mode=3）**:
```xml
<Stats module="StatsSimpleV5" mode="3"
    tradePrice="close" tax="0." fee="0." slippage="0."
    printStats="true" dumpPnl="true" pnlDir="./pnl/"/>
```

**Delay=0 专用**:
```xml
<Stats module="StatsSimpleD0"
    tradePrice="close" tax="0." fee="0." slippage="0."
    printStats="true" dumpPnl="true" pnlDir="./pnl/"/>
```

## Alpha（因子实例）

在 `<Portfolio>` 内引用注册的 Alpha 模块。Schema 要求 `id`/`module`/`universeId` 必填，`delay`/`dumpAlphaDir`/`dumpAlphaFile` 可选。

```xml
<Alpha id="AlphaWbaiReversal" module="AlphaWbaiReversalMod"
    universeId="ALL_TRD"
    booksize="20e6" delay="0" ndays="20" st="20"
    dumpAlphaFile="false" dumpAlphaDir="/tmp/AlphaWbaiReversal/alpha_dump">
    
    <Description name="AlphaWbaiReversal"
        author="wbai" birthday="20061219"
        category="5min_price_volume" universe="ALL_TRD" delay="0"/>
    
    <Operations>
        <Operation module="AlphaOpPower" exp="2"/>
        <Operation module="AlphaOpDecay" days="3"/>
        <Operation module="AlphaOpRank" exp="1.0"/>
        <Operation module="AlphaOpIndNeut" group="sector"/>
    </Operations>
</Alpha>
```

| 参数 | 必填 | 说明 |
|-----|-----|------|
| `id` | 是 | 因子实例 ID |
| `module` | 是 | 引用 Modules 中注册的 Alpha id |
| `universeId` | 是 | 引用 Modules 中注册的 Data id |
| `delay` | 否 | 延迟交易天数（0/1/2...） |
| `dumpAlphaFile` | 否 | 是否生成因子值文件（默认 false） |
| `dumpAlphaDir` | 否 | 因子值文件路径 |
| 用户自定义 | 否 | 任意自定义参数（如 `booksize`、`ndays`、`st`） |

### Description（必填子元素）

Schema 中 `<Description>` 的所有属性都是 `use="required"`：

| 属性 | 说明 |
|-----|------|
| `name` | 因子名 |
| `author` | 作者（unix id） |
| `birthday` | 出生日期（用于历史回看） |
| `universe` | 标的池 |
| `category` | 类别（如 `price`、`5min_price_volume`） |
| `delay` | 延迟天数 |

### Operations（后处理链）

`<Operations>` 包含多个 `<Operation>`，按声明顺序应用：

```xml
<Operations>
    <Operation module="AlphaOpPower" exp="2"/>
    <Operation module="AlphaOpDecay" days="3"/>
    <Operation module="AlphaOpRank" exp="1.0"/>
    <Operation module="AlphaOpIndNeut" group="sector"/>
</Operations>
```

各 Operation 的参数：

| Operation | 参数 |
|-----------|------|
| `AlphaOpDecay` | `days` |
| `AlphaOpRank` | `exp`（幂次） |
| `AlphaOpPower` | `exp`（幂次）、`rank`（是否先排序，默认 true） |
| `AlphaOpPower9` | - |
| `AlphaOpHump` | - |
| `AlphaOpIndNeut` | `group`（分组 Data id，如 `sector`/`industry`）、`minElm`（最小元素数，默认 2）、`delay` |
| `AlphaOpVectorNeutralize` | `factor`（向量 Data id）、`transform`（`log`/`sqrt`/`rank`/无）、`delay` |
| `AlphaOpNormalize` | -（z-score 标准化） |
| `AlphaOpWinsorize` | `std`（缩尾标准差倍数，支持空格分隔多次，如 `"6.0 4.0"`） |

## Alphas（组合容器）

`<Alphas>` 用于将多个 Alpha 通过 Combo 组合。Schema 要求 `id` 和 `combo` 必填。

```xml
<Alphas id="MyComboGroup" combo="Combo_bj202">
    <Description name="MyComboGroup" author="wbai" birthday="20060101"
        universe="ALL_TRD" category="combo" delay="1"/>
    <Alpha id="Alpha1" module="AlphaMod1" universeId="ALL_TRD" delay="1">
        <Description .../>
    </Alpha>
    <Alpha id="Alpha2" module="AlphaMod2" universeId="ALL_TRD" delay="1">
        <Description .../>
    </Alpha>
    <Operations>
        <!-- 组合层后处理 -->
    </Operations>
</Alphas>
```

`combo` 引用 Modules 中注册的 Combo id。

## FeatureReader（AlphaLoadFeat）

从 `alpha_feature` 加载已有因子值，替代 `alpha_dump` 的小文件方式。

```xml
<Alpha id="AlphaFromFeature" module="AlphaLoadFeat"
    universeId="ALL_TRD"
    featDir="/mnt/storage/alphalib/alpha_feature"
    ver="1" lag="0" demean="true">
    <Description name="AlphaFromFeature" author="wbai" birthday="20240101"
        universe="ALL_TRD" category="loaded" delay="1"/>
</Alpha>
```

| 参数 | 默认 | 说明 |
|-----|------|------|
| `featDir` | `feats` | feature 文件目录 |
| `ver` | `'1'` | 版本号（决定加载 `{id}.{ver}.npy`） |
| `lag` | `0` | 延迟偏移 |
| `demean` | `true` | 是否对加载值减去均值 |

文件路径模式：`{featDir}/{alphaId}.{ver}.npy`。

详见 [gsim-changelog.md](gsim-changelog.md)。

## 高级特性

### Macros（宏定义）

可选的宏定义区，schema 允许任意属性：

```xml
<Macros startdate="20150101" enddate="20241231" booksize="20e6"/>
```

### Optimize（优化配置）

Schema 支持，配合 `Optimize.modulePath` 加载优化模块：

```xml
<Optimize id="MyOpt" modulePath="/path/to/optimizer.py">
    <!-- 优化器特定配置 -->
</Optimize>
```

### IntradayCurve（日内曲线）

用于日内策略，schema 要求 `name` 必填：

```xml
<IntradayCurve name="MyCurve">
    <Config><!-- 曲线参数 --></Config>
</IntradayCurve>
```

### 嵌套 Config

`<Config>` 元素可包含任意子元素，用于扩展模块配置：

```xml
<Alpha id="AlphaX" module="AlphaModX" universeId="ALL_TRD">
    <Config>
        <Param key="window" value="20"/>
        <Param key="method" value="ewm"/>
    </Config>
</Alpha>
```

通过 `Config` 类访问：`cfg.find('Config').find('Param')`。

## 完整可运行示例

参考 `/datasvc/template/AlphaWbaiReversal/Config.Wbai.Reversal.xml`：

```xml
<gsim>
  <Constants backdays="256" niodatapath="/datasvc/data/cc" niomapprivate="true"
    authorWeight="wbai:1.0," time_intensive="false"/>
  <Universe startdate="20170101" enddate="20241231"
    secID="/datasvc/rawdata/secID"
    holidaysfile="/datasvc/rawdata/holidays"
    calendarfile="/datasvc/rawdata/wind_calendar.csv"/>
  
  <Modules>
    <Data id="ALL" module="UmgrAll" path=""/>
    <Data id="ALL_TRD" module="UmgrTrd" path=""/>
    <Data id="Basedata" module="DmgrBasedata"
      rawpricePath="" industryPath="" ST="" path="" niomapprivate="true"/>
    <Data id="PriceLimit" module="DmgrPriceLimit" dataPath="" path=""/>
    <Data id="adjfactor" module="DmgrAdjfactor" dataPath="" niomapprivate="true" path=""/>
    <Data id="adjprice" module="DmgrAdjprice" niomapprivate="true" path=""/>
    <Data id="ipo" module="DmgrIPO" dataPath="" path=""/>
    <Data id="ashareeodprices" module="Dmgrashareeodprices" dataPath="" niomapprivate="true"/>
    <Data id="aindexeodprices" module="Dmgraindexeodprices" dataPath="" niomapprivate="true"/>
    <Data id="Interval5m" module="/usr/local/gsim/source_ref/interval_5m_zx.py" dataPath="" path=""/>

    <Alpha id="AlphaWbaiReversalMod"
      module="/datasvc/template/AlphaWbaiReversal/AlphaWbaiReversal.py"/>
  </Modules>

  <Portfolio id="MyPort" booksize="20e6" homecurrency="CNY">
    <Stats module="StatsSimpleV5" mode="0"
      tradePrice="close" tax="0." fee="0." slippage="0."
      printStats="true" dumpPnl="true"
      pnlDir="/tmp/AlphaWbaiReversal/alpha_pnl"/>

    <Alpha id="AlphaWbaiReversal" module="AlphaWbaiReversalMod"
      universeId="ALL_TRD"
      booksize="20e6" delay="0" ndays="20" st="20"
      dumpAlphaFile="false"
      dumpAlphaDir="/tmp/AlphaWbaiReversal/alpha_dump">
      <Description name="AlphaWbaiReversal" author="wbai" birthday="20061219"
        category="5min_price_volume" universe="ALL_TRD" delay="0"/>
      <Operations>
        <Operation module="AlphaOpPower" exp="2"/>
        <Operation module="AlphaOpDecay" days="3"/>
        <Operation module="AlphaOpRank" exp="1.0"/>
        <Operation module="AlphaOpIndNeut" group="sector"/>
      </Operations>
    </Alpha>
  </Portfolio>
</gsim>
```

## 注意事项

1. **绝对路径**: 建议 `dumpAlphaDir`、`pnlDir`、`module` 使用绝对路径，避免相对路径解析问题
2. **空 dataPath**: `<Data>` 的 `dataPath` 和 `path` 通常留空，使用模块默认路径
3. **Schema 校验**: `run.py` 会用 `gsim.xsd` 校验配置，必填字段缺失会立即报错
4. **Description 必填属性**: 所有 6 个属性都是 required，遗漏会校验失败
5. **DO NOT 信任 XML `<Data>`** 作为因子数据源依赖，实际依赖需解析 Python 代码中的 `dr.getData()` 调用
6. **回测周期**: 完整 `20150101-20241231`，简化 `20190101-20241231`
