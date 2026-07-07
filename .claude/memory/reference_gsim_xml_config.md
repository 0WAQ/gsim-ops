---
name: reference-gsim-xml-config
description: "gsim 回测 XML config 骨架 (Constants / Universe / Modules), Data module 两种写法 (.py 全路径 vs 裸 class), niodatapath vs dataPath 区分, niomapprivate 读写标志"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 7ff88a50-5f38-42d3-a39c-f97ddef36c12
---

# gsim 回测 XML config 结构

参考模板: `/datasvc/template/config.read_cache.xml` (read-only 全量 cache 模板)。

## 顶层骨架

```xml
<gsim>
  <Constants .../>              <!-- session-wide 默认值 -->
  <Universe .../>               <!-- 时间窗 + 股票 universe + 日历 -->
  <Modules>
    <Data .../>                 <!-- 一堆, 数据 module -->
    <Alpha .../>                <!-- 因子 module -->
    <Combo .../>                <!-- 组合 module, .py 或 .so -->
  </Modules>
  <Portfolio booksize="..." homecurrency="CNY">
    <Stats module="..." .../>   <!-- 统计 / PNL dump -->
  </Portfolio>
</gsim>
```

`Data` / `Alpha` / `Combo` 都在同一个 `<Modules>` 块里, 说明 gsim runtime 视角下三者是 module 的不同子类型, 注册机制统一。

## Constants — session 级默认

```xml
<Constants backdays="256" niodatapath="/datasvc/data/cc" 
           niomapprivate="true" authorWeight="ywang:1.0," 
           time_intensive="false"/>
```

- `niodatapath`: **session 级默认 cc 根**, 单个 `<Data>` 可以用自己的 `niodatapath` 覆盖
- `niomapprivate`: session 级默认读写标志, 单个 `<Data>` 不写就继承
- `backdays`: 因子计算的回看窗口 (天)
- `authorWeight`: combo 加权相关, 具体语义不明 (待澄清)
- `time_intensive`: 性能 flag

**`/datasvc/data/cc -> cc_2024` 是软链**, 所以 read-only template 默认指向 2024 T 轴快照 by design (T 轴定格在 20241231, 但仍可 backfill 新 feature 类型, 见 [[reference-cc-all-data-layout]])。要跑 cc_all 得在 Constants 或单个 `<Data>` 里显式覆盖。

## Universe — 时间窗 + universe + 日历

```xml
<Universe startdate="20110101" enddate="20241231" 
          secID="/datasvc/rawdata/secID" 
          holidaysfile="/datasvc/rawdata/holidays" 
          calendarfile="/datasvc/rawdata/wind_calendar.csv"/>
```

- `startdate` / `enddate`: T 轴回测范围
- `secID`: 股票 ID mapping (`/datasvc/rawdata/secID`, N=5484)
- `calendarfile`: 交易日历 (`/datasvc/rawdata/wind_calendar.csv`)
- `holidaysfile`: 节假日列表

周末 / 节假日 T 轴**不 append**, 严格按 wind_calendar 来。

## `<Data>` module 两种写法

| 写法 | 例子 | 含义 |
|---|---|---|
| 全路径 `.py` | `module="/usr/local/gsim/source_ref/Dmgr_xxx.py"` | runtime 动态 import 文件, 按 stem ≈ class 名取 |
| 裸 class 名 | `module="UmgrAll"`, `module="DmgrAdjfactor"`, `module="Dmgrequ_factor_growth"` | gsim builtin 注册表里直接查 (在 `gsim/data/module/` 内置包) |

两种并存。外部贡献写全路径, gsim 自带模块写 class 名。

## 关键属性

| 属性 | 例子 | 含义 |
|---|---|---|
| `id` | `id="aindexeodprices"` | 在 XML 内引用这个 Data 的 key (粗粒度) |
| `module` | `module=".../Dmgr_xxx.py"` | module 文件 / class |
| `dataPath` | `dataPath="/datasvc/rawdata/rawdata_wind/xxx/"` | **rawdata 源路径** (CSV 在哪) |
| `niodatapath` | `niodatapath="/datasvc/data/cc/cn_equity_feature/"` | **cc 落地路径覆盖** (默认从 Constants 继承) |
| `niomapprivate` | `"true"` / `"false"` | `true`=读, `false`=写 |
| `path=""` | `path=""` | legacy, 留空没事 |
| `univsize` | `univsize="2600"` | universe 大小 (TOP系列) |
| `dataPath` (level2) | `dataPath="/datasvc/data/cc/cn_equity_feature/"` | level2 adapter 用这个传 cc 子路径 |

**`niodatapath` vs `dataPath` 别搞混**: 前者是 cc 落地处 (gsim 写出 / 读入), 后者是 rawdata 源 (CSV 之类输入)。

## `niomapprivate` 读写语义 (重要)

```
niomapprivate="true"   → 读模式 (dataloader 路径)
                          - runtime 调 initialize 注册 memmap
                          - loadData / loadDay 不被调用 (空跑也行)
                          - 因子 dr.getData('xxx') 走这条

niomapprivate="false"  → 写模式 (data-writer 路径)
                          - runtime 算 di_start (根据 .meta 水位 vs cfg endDate)
                          - 从 di_start 调 loadData(di_start) 或循环 loadDay(di) 填充
                          - 把 NIO_MATRIX 写回盘
```

同一份 module 文件可以两侧共用, 切换靠 XML flag。详见 [[reference-gsim-data-modules]]。

## 几个静默坑

1. **属性名 typo**: 见过 `nioimapprivate` (line 133/137, 实际 read_cache.xml), 静默 fallback 到 Constants 默认, 不报错。写 XML 时拼写错了不会有任何提示。
2. **大量 `<!-- ... -->` 注释整块**: read_cache.xml 里 hf_daily_*、Dpv 整组、level2 fguo/zzk/yq/fb 整组、signal_rsh 都注释掉了。说明这个 template 按"够用就行"裁剪, 全量在别的 config。
3. **`path=""` 到处都是**: 看起来是 legacy 参数, 留空就行。
4. **裸 class 名 vs 全路径混存**: 同一个 XML 里 `module="UmgrAll"` 和 `module="/usr/local/gsim/source_ref/Dmgr_xxx.py"` 并存, 不要假设统一约定。

## 因子端 / Combo 端

- `<Alpha id="X" module="/usr/local/gsim/alpha_src/X.py"/>`
- `<Combo id="X" module=".../X.cpython-310-x86_64-linux-gnu.so"/>` — combo 可以是 cython 编译产物 `.so`, 也可以是 .py 源码

## Portfolio + Stats

```xml
<Portfolio id="MyPort" booksize="20e6" homecurrency="CNY">
  <Stats module="StatsSimple" tradePrice="close" tax="0." 
         fee="0." slippage="0." printStats="true" 
         dumpPnl="true" pnlDir="./pnl/"/>
</Portfolio>
```

- `booksize`: 资金规模 (CNY)
- `tradePrice`: 撮合价格 (close / open / vwap 等)
- `dumpPnl` + `pnlDir`: PNL 落盘开关 + 目录
- Stats module: read_cache.xml 用的是 `StatsSimple`, ops 当前用 `StatsSimpleV6` (更新版本)

## ops 那条铁律

CLAUDE.md "不信 XML `<Data>` 声明, 要解析 Python 里 `dr.getData('xxx')`" 的工程理由:

1. XML 只声明粗粒度 `id`, 真正 feature tag 在 module 内部 `addDailyData(matrix, tag)` 决定, 一对多
2. 属性 typo (`nioimapprivate`) 静默 fallback, XML 不可信
3. 注释掉的块在 XML 里照样存在, parse 不容易过滤
4. 裸 class 名 vs 全路径混存, 没法静态对应到具体源文件
5. dr.getData() 是真触达数据的入口, 才是 ground truth

详见 [[reference-gsim-data-modules]]。

相关:
- [[reference-gsim-data-modules]] — module 写法 + NIO_MATRIX + tag namespace
- [[reference-company-data-architecture]] — rawdata / cc / dm / feature 数据架构
- [[reference-cc-all-data-layout]] — cc_all 物理 layout
- [[gsim-architecture]] — gsim 整体目录 / 工具链
