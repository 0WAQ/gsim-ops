# Gsim 架构说明

Gsim 是位于 `/usr/local/gsim/` 的量化因子回测框架，是 ops 交互的核心引擎。

## 目录结构

```
/usr/local/gsim/
├── gsim/              # 核心模块（Python + C++ .so）
│   ├── alpha/         # 因子基类、加载器和 module 子模块
│   ├── combo/         # 组合模块（等权/静态/简单/回归 4 种）
│   ├── data/          # 数据管理（DataRegistry, Universe, 各 Dmgr）
│   ├── stats/         # 统计模块（StatsSimpleV5 等 12 个）
│   ├── operator/      # 因子后处理（Decay/Rank/IndNeut 等 9 个）
│   ├── utils/         # 工具函数（Config, Oputil, Calendar, NioData）
│   ├── gsim.xsd       # XML 配置 schema
│   └── *.so           # C++ 编译模块（Portfolio, Checkpoint 等）
├── tools/             # 分析工具
│   ├── simsummary.py  # PNL 汇总
│   └── bcorr.py       # 相关性测试（Python 版，多进程）
├── dataops/           # 编译工具
│   └── bcorr          # 相关性测试（C++ 编译版，更快）
├── alpha_src/         # 因子源代码示例（Alphasize.py、prod_npy_load.py、prod_npy_load_con.py）
├── combo_src/         # 组合源代码示例
├── dm_src/            # 自定义数据模块源代码
├── source_ref/        # 数据模块的 Python 源码参考
├── docs/              # 内置文档（stats.md 等）
├── pnl_prod/          # 生产环境 PNL 池（相关性比较基准）
├── pnl_pool/          # delay=1 PNL 池
├── pnl_pool_d0/       # delay=0 PNL 池
├── pnl_pool_llm/      # LLM 相关 PNL 池
├── run.py             # 标准回测入口
├── run_cp.py          # 支持 checkpoint 的回测入口
└── .venv/             # Python 虚拟环境
```

## 入口脚本

| 脚本 | 用途 | 何时使用 |
|-----|------|---------|
| `run.py` | 标准回测 | 一般场景 |
| `run_cp.py` | 支持 checkpoint 的回测 | 验证因子断点恢复能力（CheckPoint 检测） |

`run_cp.py` 会在 `endIndex - checkpointDays` 时保存 checkpoint，下次启动会从 checkpoint 恢复，用于验证因子在重启后能否产出一致的结果。

调用方式：
```bash
/usr/local/gsim/.venv/bin/python /usr/local/gsim/run.py config.xml
/usr/local/gsim/.venv/bin/python /usr/local/gsim/run_cp.py config.xml
```

## 核心命令

```bash
# 回测
/usr/local/gsim/.venv/bin/python /usr/local/gsim/run.py config.xml

# PNL 汇总
/usr/local/gsim/.venv/bin/python /usr/local/gsim/tools/simsummary.py /path/to/pnl

# 相关性测试（C++ 二进制，推荐）
/usr/local/gsim/dataops/bcorr pnl1 pnl2
/usr/local/gsim/dataops/bcorr pnl1 /usr/local/gsim/pnl_prod/

# 相关性测试（Python 版，备选）
/usr/local/gsim/.venv/bin/python /usr/local/gsim/tools/bcorr.py pnl1 pnl2
```

## 核心模块

### AlphaBase（因子基类）

所有因子继承自 `AlphaBase`，源码位于 `gsim/alpha/alpha_base.py`：

```python
class AlphaBase(Serializable, metaclass=ABCMeta):
    def __init__(self, cfg):
        self.universeId = cfg.getAttributeString('universeId')
        self.delay = cfg.getAttributeDefault('delay', 1)
        self.intraday = cfg.getAttributeDefault('intraday', False)
        self.valid = dr.getData(self.universeId)
        self.alpha = np.full(len(uv.Instruments), np.nan)

    def generate(self, di):
        raise NotImplementedError('generate must be implemented for daily alpha')

    def generate_ti(self, di, ti):
        self.generate(di)

    def reset(self, val=np.nan):
        self.alpha[:] = val
```

关键属性：
- `self.universeId`: 标的池 ID
- `self.delay`: 延迟天数（默认 1）
- `self.intraday`: 是否日内（默认 False）
- `self.valid`: 标的有效性矩阵
- `self.alpha`: 当日因子值数组（大小 = `len(uv.Instruments)`）

### Alpha 加载模块

`gsim/alpha/__init__.py` 导出 4 个 Alpha 类：

| 类 | 源文件 | 用途 |
|---|-------|------|
| `AlphaBase` | `alpha_base.py` | 自定义因子的基类 |
| `AlphaMatFile` | `module/` | 从 mat 文件加载因子 |
| `AlphaLoad` | `module/alpha_load.py` | 从 `alpha_dump`（小文件）加载 |
| `AlphaLoadFeat` | `module/alpha_load_feature.py` | 从 `alpha_feature`（聚合大文件）加载，**即 FeatureReader** |

`AlphaLoadFeat` 是 alpha_dump → alpha_feature 迁移的核心，详见 [changelog.md](changelog.md)。

### Stats 模块

`gsim/stats/__init__.py` 导出 1 个基类 + 12 个统计模块：

| 模块 | 用途 |
|-----|------|
| `StatsBase` | 基类 |
| `StatsSimple` | 基础统计（默认模板使用） |
| `StatsSimpleV5` | V5 多模式（mode=0/1/2/3） |
| `StatsSimpleD0` | delay=0 专用 |
| `StatsSimple2` | V2 变种 |
| `StatsSimpleX` | X 变种 |
| `StatsLS` | Long Short（naive 版） |
| `StatsLong` | 纯多头 |
| `StatsLongShort` | 多空 |
| `StatsBench` | 指数增强（mode=1 等价） |
| `StatsBenchLayer` | 分层统计（mode=2 等价） |
| `StatsIndexGIM` | 指数 GIM 长仓 |
| `StatsOptV5` | 优化版 V5 |

`StatsSimpleV5` 的 mode 参数（来自 `docs/stats.md`）：
- `mode=0`: Long Short（等价 StatsSimple）
- `mode=1`: StatsBench（指数增强）
- `mode=2`: StatsBenchLayer（分层，`thres=90` 表示 top 10%）
- `mode=3`: Long Only

### Operator 模块

`gsim/operator/__init__.py` 导出 9 个 operator：

| 模块 | 主要参数 | 用途 |
|-----|---------|------|
| `AlphaOpBase` | - | 基类 |
| `AlphaOpDecay` | `days` | 时间衰减 |
| `AlphaOpRank` | `exp` | 排序变换（exp=幂次） |
| `AlphaOpPower` | `exp`, `rank` | 幂次变换 |
| `AlphaOpPower9` | - | Power9 变换 |
| `AlphaOpHump` | - | Hump 平滑 |
| `AlphaOpIndNeut` | `group`, `minElm`, `delay` | 行业/分组中性化 |
| `AlphaOpVectorNeutralize` | `factor`, `transform`, `delay` | 向量中性化（支持 log/sqrt/rank 变换） |
| `AlphaOpNormalize` | - | 标准化（z-score） |
| `AlphaOpWinsorize` | `std` | 缩尾处理（默认 4 倍标准差） |

源文件位于 `/usr/local/gsim/gsim/operator/*.py`。

### Combo 模块

`gsim/combo/__init__.py` 导出 4 种组合方式 + 基类：

| 模块 | 源文件 | 用途 |
|-----|-------|------|
| `ComboBase` | `combo_base.py` | 基类 |
| `AlphaComboEqual` | `combo_equal.py` | 等权组合 |
| `AlphaComboStatic` | `combo_static.so` | 静态权重组合 |
| `AlphaComboSimple` | `combo_simple.so` | 简单组合 |
| `AlphaComboRegression` | `combo_linear.so` | 回归组合 |

`AlphaComboEqual` 是源码可读的实现，可作为编写自定义 combo 的参考。`combo_src/` 下的项目级 combo（如 `Combo_bj202.so`、`Combo_sz102.so`、`Combo_su8.so`）均为编译模块，源码不可见。

### Data 模块

`gsim/data/module/__init__.py` 导出 50+ 个 Dmgr 类。详细列表见 [data-sources.md](data-sources.md)。

按类别概览：
- **Universe 类**: `UmgrAll`, `UmgrTrd`（基础），`umgr_full.py`/`umgr_gim.py`（扩展），`source_ref/umgr_index.py`（指数），`source_ref/umgr_topliquid.py`（流动性 TOP）
- **基础数据**: `DmgrBasedata`, `DmgrIPO`, `DmgrPriceLimit`, `DmgrAdjfactor`, `DmgrAdjprice`
- **行情数据**: `Dmgrashareeodprices`, `Dmgraindexeodprices`, `DmgrInterval5m`, `DmgrAShareMoneyFlow`
- **财务数据**: `Dmgrasharebalancesheet`, `Dmgrashareincome`, `Dmgrasharecashflow`
- **因子数据**: `Dmgrequ_factor_*`（oc/growth/power/cf/psi/sc/vs/return/volume/trend/pq/derive/obos/ma/af），共 15 个
- **Fancy 因子**: `Dmgrequ_fancy_factors_table1~8`（gsim 内置 8 个，配置中可注册到 table10）
- **一致预期**: `Dmgrashareconsensusrollingdata_*`（CAGR/FTTM/FY0/FY1/FY2/FY3/YOY/YOY2）
- **自定义 dpv 系列**: `DmgrDpv`, `DmgrDpva`, `DmgrDpvb`, `DmgrDpvc`, `DmgrDpvd`, `DmgrDipv`, `DmgrDipva`
- **AI 预测**: 9 个 `*_fore_*` 模块（需在配置中手动注册，源码在 `source_ref/`）

完整数据源配置示例可参考 `/datasvc/template/config.read_cache.xml`。

### DataRegistry

`dr.getData()` 是访问数据的统一接口，源码为 `gsim/data/DataRegistry.so`（C++ 编译）。

返回类型：
- 二维矩阵（日频）：形状 `(n_dates, n_stocks)`
- 三维立方（分钟频）：形状 `(n_dates, n_bars, n_stocks)`

### Universe

`gsim/data/Universe.so` 提供回测时间和股票池管理：
- `uv.Dates`: 交易日数组
- `uv.Instruments`: 股票代码数组（共 5484 个，FeatureReader 中硬编码）
- `uv.startIndex` / `uv.endIndex`: 回测起止索引

### Config

`gsim/utils/Config.py` 是 XML 节点的封装：
- `getAttributeString(key)`: 必需属性（缺失抛错）
- `getAttributeStringDefault(key, val)`: 字符串默认值
- `getAttribute(key)`: 自动类型推断（int/float/bool）
- `getAttributeDefault(key, val)`: 类型推断 + 默认值

## 数据缓存系统

数据缓存位于 `/datasvc/data/cc/`（只读），通过 `dr.getData('source.field')` 访问。

### 二维矩阵（Matrix Data Cache）

日频数据，形状 `(n_dates, n_stocks)`：

```python
self.s_dq_close = dr.getData('ashareeodprices.s_dq_close')
def generate(self, di):
    close = self.s_dq_close[di - self.delay, ii]
```

### 三维立方（Cube Data Cache）

分钟频数据，形状 `(n_dates, n_bars, n_stocks)`：

```python
self.close_m5 = dr.getData('Interval5m.close')
def generate(self, di):
    # ti 范围: 0=集合竞价, 1-48=9:30 后每 5 分钟
    bar = self.close_m5[di - self.delay, 48, valid_idx]
```

完整数据源列表见 [data-sources.md](data-sources.md)。

## 性能关键模块

以下 `.so` 文件是 C++ 编译的性能关键模块：

| 文件 | 模块 |
|-----|------|
| `gsim/gsim_base.so` | Serializable 基类 |
| `gsim/gsim_portfolio.so` | Portfolio |
| `gsim/gsim_checkpoint.so` | Checkpoint 机制 |
| `gsim/alpha_node.so` | AlphaNode 节点树 |
| `gsim/data/DataRegistry.so` | 数据注册中心 |
| `gsim/data/Universe.so` | 标的池管理 |
| `gsim/data/dmgr_base.so` | DataManager 基类 |
| `gsim/data/module/*.so` | 各类 Dmgr 数据模块 |
| `gsim/stats/*.so` | 各类 Stats 统计模块 |
| `gsim/operator/*.so` | 各类 Operator 后处理 |
| `gsim/combo/*.so` | Static/Simple/Linear Combo |

源码不可见的模块，参数说明需参考 `gsim.xsd` schema 或实际配置示例。

## 参考资料

- XML 配置详细说明：[xml-config.md](xml-config.md)
- 因子开发流程：[factor-workflow.md](factor-workflow.md)
- 因子入库检测：[factor-validation.md](factor-validation.md)
- 数据源参考：[data-sources.md](data-sources.md)
- 更新日志：[changelog.md](changelog.md)

> 本文档基于 `/usr/local/gsim` 的实际代码整理，但 gsim 持续演进，模块列表可能滞后。实际开发时，以 `gsim/*/__init__.py` 中的导入声明为准。
