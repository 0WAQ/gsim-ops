# Gsim 因子开发流程

本文档说明从因子开发到入库的完整流程。

## 一、环境准备

### 1.1 服务器信息

- **服务器 IP**: 10.6.100.146
- **用户名**: {Your Unix ID}
- **密码**: {Your Password}

### 1.2 核心目录

| 目录类型 | 路径 | 说明 |
|---------|------|-----|
| HOME 目录 | `/mnt/storage/work/{Your Unix ID}/` | 个人工作主目录 |
| 公共数据缓存 | `/datasvc/data/cc/` | 只读权限，禁止修改 |
| 自定义数据模块 | `/mnt/storage/data_dmgr/` | Lzhang & Wbai 自定义数据源 |
| 因子库 | `/mnt/storage/alphalib/` | 因子源代码、PNL、dump、feature |
| Dropbox | `/mnt/storage/dropbox/{Unix ID}/` | 因子提交入口 |

### 1.3 模板与示例

| 文件/目录 | 用途 |
|---------|------|
| `/datasvc/template/AlphaWbaiReversal/` | 完整模板因子（py + xml + md） |
| `/datasvc/template/config.read_cache.xml` | 全数据源注册示例 |
| `/datasvc/template/config.build_cache.xml` | 数据缓存构建模板 |
| `/usr/local/gsim/alpha_src/Alphasize.py` | 极简因子示例 |

### 1.4 回测周期

- **完整周期**: 20150101 - 20241231
- **简化周期（建议）**: 20190101 - 20241231

## 二、因子开发

### 2.1 因子代码结构

参考 `/datasvc/template/AlphaWbaiReversal/AlphaWbaiReversal.py`：

```python
from gsim import Universe as uv
from gsim import DataRegistry as dr
from gsim import AlphaBase
from gsim import Oputil
import numpy as np


class AlphaWbaiReversal(AlphaBase):
    def __init__(self, cfg):
        AlphaBase.__init__(self, cfg)
        self.vol = dr.getData('volume').data
        self.close = dr.getData('yq_212_5min.close').data
        return

    def generate(self, di):
        valid_idx = self.valid[di] & (self.vol[di - 1] > 0)
        bar_1  = self.close[di - self.delay, 1, valid_idx]
        bar_42 = self.close[di - self.delay, 42, valid_idx]
        self.alpha[valid_idx] = bar_1 / bar_42
        return
```

关键点：
- 继承 `AlphaBase`，调用基类 `__init__(cfg)`
- 在 `__init__` 中初始化数据（`dr.getData(...)`）
- 实现 `generate(self, di)` 方法（或 `generate_ti(self, di, ti)` 支持日内）
- `self.alpha` 是当日因子值数组（形状 = `(len(uv.Instruments),)`）
- `self.valid[di]` 是当日标的有效性掩码
- `self.delay` 来自 XML 配置（默认 1）

> 模板示例使用了 `yq_212_5min` 这类自定义 Level2 数据源，在 `config.read_cache.xml` 默认是注释状态。仿写时若用到此类数据，务必在自己的 Config XML 的 `<Modules>` 内手工注册对应 `<Data>`（参考 `Config.Wbai.Reversal.xml` L19）。

`AlphaBase` 的完整接口见 [gsim-architecture.md](gsim-architecture.md#alphabase因子基类)。

### 2.2 数据访问

#### 二维矩阵（日频）

```python
self.s_dq_close = dr.getData('ashareeodprices.s_dq_close')

def generate(self, di):
    close_price = self.s_dq_close[di - self.delay, ii]
```

#### 三维立方（分钟频）

```python
self.close_m5 = dr.getData('Interval5m.close')

def generate(self, di):
    # ti 范围: 0=集合竞价, 1-48=9:30 后每 5 分钟
    bar = self.close_m5[di - self.delay, 48, valid_idx]
```

完整数据源列表见 [gsim-data-sources.md](gsim-data-sources.md)。

### 2.3 命名规范

因子命名：`Alpha{UnixId}{Name}`

- `UnixId` 大小写敏感，习惯首字母大写（如 `Wbai`）
- `Name` 以大写字母或数字开头

示例：`AlphaWbaiReversal`、`AlphaJzhang20260324GA002`

### 2.4 Checkpoint 支持

如果因子使用跨日状态变量（如 `self.prev`），必须实现 checkpoint 方法：

```python
import pickle

class AlphaExample(AlphaBase):
    def __init__(self, cfg):
        AlphaBase.__init__(self, cfg)
        self.prev = None
    
    def checkpointSave(self, fh):
        pickle.dump(self.prev, fh)
    
    def checkpointLoad(self, fh):
        self.prev = pickle.load(fh)
        
    def generate(self, di):
        if self.prev is None:
            self.prev = alloc_some_bytes()
        else:
            self.prev = some_calculate(...)
        self.alpha = some_calculate(self.prev)
```

**为什么需要**: 生产环境可能随时重启。`run_cp.py` 会在 `endIndex - checkpointDays` 时保存 checkpoint，重启后从 checkpoint 恢复继续执行。详见 [gsim-factor-validation.md](gsim-factor-validation.md#2-checkpoint断点恢复检测)。

## 三、因子回测

### 3.1 配置文件

参考 `/datasvc/template/AlphaWbaiReversal/Config.Wbai.Reversal.xml`，完整配置说明见 [gsim-xml-config.md](gsim-xml-config.md)。

最小模板：

```xml
<gsim>
    <Constants backdays="256" niodatapath="/datasvc/data/cc" niomapprivate="true"/>
    <Universe startdate="20190101" enddate="20241231"
        secID="/datasvc/rawdata/secID"
        holidaysfile="/datasvc/rawdata/holidays"
        calendarfile="/datasvc/rawdata/wind_calendar.csv"/>
    
    <Modules>
        <Data id="ALL_TRD" module="UmgrTrd" path=""/>
        <!-- 其他必要 Data 模块 -->
        <Alpha id="MyAlphaMod" module="/path/to/MyAlpha.py"/>
    </Modules>
    
    <Portfolio id="MyPort" booksize="20e6" homecurrency="CNY">
        <Stats module="StatsSimpleV5" mode="0"
            tradePrice="close" tax="0." fee="0." slippage="0."
            printStats="true" dumpPnl="true" pnlDir="./pnl/"/>
        
        <Alpha id="MyAlpha" module="MyAlphaMod" universeId="ALL_TRD"
            delay="1" dumpAlphaFile="false">
            <Description name="MyAlpha" author="wbai" birthday="20061219"
                category="price" universe="ALL_TRD" delay="1"/>
        </Alpha>
    </Portfolio>
</gsim>
```

### 3.2 运行回测

#### 标准回测

```bash
/usr/local/gsim/.venv/bin/python /usr/local/gsim/run.py config.xml
```

#### Checkpoint 回测

用于验证因子能否在中断后恢复（CheckPoint 检测）：

```bash
# 第一次运行：跑到 endIndex - checkpointDays 时保存 checkpoint，然后继续到 endIndex
/usr/local/gsim/.venv/bin/python /usr/local/gsim/run_cp.py config.xml

# 第二次运行：从上次 checkpoint 恢复，继续到 endIndex
/usr/local/gsim/.venv/bin/python /usr/local/gsim/run_cp.py config.xml
```

两次运行的最终 PNL 应该完全一致，否则 checkpoint 实现有问题。

## 四、因子分析

### 4.1 PNL 汇总（simsummary）

源码：`/usr/local/gsim/tools/simsummary.py`

```bash
# 基本用法
/usr/local/gsim/.venv/bin/python /usr/local/gsim/tools/simsummary.py /path/to/pnl_file

# 指定时间范围（年化分组）
/usr/local/gsim/.venv/bin/python /usr/local/gsim/tools/simsummary.py \
    -s 20200101 -e 20231231 /path/to/pnl_file

# 月度分组
/usr/local/gsim/.venv/bin/python /usr/local/gsim/tools/simsummary.py \
    -t monthly /path/to/pnl_file

# 指定无风险收益率（默认 0.15）
/usr/local/gsim/.venv/bin/python /usr/local/gsim/tools/simsummary.py \
    -r 0.03 /path/to/pnl_file
```

| 参数 | 默认 | 说明 |
|-----|------|------|
| `-s`, `--start` | -1（全部） | 开始日期 YYYYMMDD |
| `-e`, `--end` | -1（全部） | 结束日期 YYYYMMDD |
| `-t`, `--type` | `yearly` | 分组类型：`yearly` 或 `monthly` |
| `-r`, `--ret` | `0` | 无风险收益率（年化） |
| `pnl` | - | PNL 文件路径（位置参数） |

输出列：
```
dates  long(M)  short(M)  pnl(M)  %ret  %tvr  shrp (IR)  %dd  %win  fitness  ddStart  ddEnd
```

实际输出示例（来自 AlphaWbaiReversal Readme）：

```
            dates long(M) short(M)  pnl(M)    %ret    %tvr      shrp (IR)   %dd  %win fitness   ddStart     ddEnd
20150105-20151231   10.00   -10.00   4.406   43.70   53.32    3.22( 0.21) 15.03  0.65    2.91  20150615  20150708
20160104-20161230   10.00   -10.00   1.254   12.44   55.05    1.43( 0.09) 10.58  0.66    0.68  20160104  20160128

20150105-20221230   10.00   -10.00  17.191   21.37   58.34    2.29( 0.15) 15.03  0.62    1.39  20150615  20150708
```

### 4.2 相关性测试（bcorr）

有两个版本：

#### C++ 二进制（推荐，性能更好）

```bash
# 文件 vs 文件
/usr/local/gsim/dataops/bcorr pnl1 pnl2

# 文件 vs 目录（与目录内所有文件比较）
/usr/local/gsim/dataops/bcorr pnl1 /usr/local/gsim/pnl_prod/
/usr/local/gsim/dataops/bcorr pnl1 /mnt/storage/alphalib/alpha_pnl/
```

#### Python 版

源码：`/usr/local/gsim/tools/bcorr.py`

```bash
# 基本用法
/usr/local/gsim/.venv/bin/python /usr/local/gsim/tools/bcorr.py pnl1 pnl2

# 指定时间范围
/usr/local/gsim/.venv/bin/python /usr/local/gsim/tools/bcorr.py \
    pnl1 pnl_folder -s 20210101 -e 20241231
```

| 参数 | 默认 | 说明 |
|-----|------|------|
| 位置参数 1 | - | 第一个 PNL 文件 |
| 位置参数 2 | - | 第二个 PNL 文件或目录 |
| `-s` | `20180101` | 开始日期 |
| `-e` | `20231201` | 结束日期 |
| `-f` | - | 文件（覆盖位置参数） |
| `-p` | - | 第二个文件或目录（覆盖位置参数） |

输出格式：`{对比因子名} {相关性}`，按相关性升序排列。

### 4.3 常用 PNL 池

| 路径 | 用途 |
|-----|------|
| `/usr/local/gsim/pnl_prod/` | 生产环境因子 PNL 池 |
| `/usr/local/gsim/pnl_pool/` | delay=1 因子 PNL 池 |
| `/usr/local/gsim/pnl_pool_d0/` | delay=0 因子 PNL 池 |
| `/usr/local/gsim/pnl_pool_llm/` | LLM 相关因子 PNL 池 |
| `/mnt/storage/alphalib/alpha_pnl/` | 因子库的 PNL 副本 |

## 五、因子评审（前 5 个因子）

### 5.1 提交材料

发送邮件至 yong@graceim.ai 和 wenbo@graceim.ai，附件包含：

1. **因子源代码**: `Alpha{UnixId}{AlphaName}.py`
   - `AlphaName` 需以大写字母或数字开头
   - 飞书不允许上传 `.py` 文件时，可删除或修改后缀

2. **配置文件**: `Config.{UnixId}.{AlphaName}.xml`

3. **说明文档**: `Readme.{UnixId}.{AlphaName}.txt`（文件类型不限，建议 md）

### 5.2 Readme 内容要求

参考 `/datasvc/template/AlphaWbaiReversal/Readme.Wbai.Reversal.md`，需包含：

- **因子思路**: 经济/统计直觉
- **因子公式**: 数学表达式
- **PNL 汇总**: `simsummary` 完整输出
- **相关性测试**: `bcorr` 输出（与因子池前若干名）

正文同步粘贴邮件内容。

## 六、因子提交（通过 ops）

### 6.1 提交路径

```
/mnt/storage/dropbox/{UnixId}/{Date}/Alpha{UnixId}{Name}/
```

示例：`/mnt/storage/dropbox/wbai/20251231/AlphaWbaiReversal/`

文件清单：
```
AlphaWbaiReversal/
├── AlphaWbaiReversal.py
├── Config.Wbai.Reversal.xml
└── Readme.Wbai.Reversal.txt    # 或 .md
```

### 6.2 使用 ops 提交

```bash
# 提交一天的所有因子
uv run ops submit -u wbai -s 20260401

# 提交单个因子
uv run ops submit -u wbai -s 20260401 -f AlphaWbaiReversal
```

## 七、因子入库检测

提交后，因子进入 `ops check` 的验证管道。完整流程见 [gsim-factor-validation.md](gsim-factor-validation.md)。

### 7.1 入库标准摘要

阈值由 `config.yaml` 统一控制(tvr 按 delay 区分):

| 项目 | 标准 |
|-----|------|
| 年化收益率 (ret%) | ≥ 10% |
| 换手率 (tvr%) | ≤ 50 (delay=1) / ≤ 60 (delay=0) |
| 夏普比率 (shrp) | > 2.00 |
| 最大相关性 | < 0.7 |

仓位约束：
- 个股最大持仓 ≤ 5%
- 多/空最小持股数 ≥ 50
- 总最小持股数 ≥ 100

### 7.2 查看状态

```bash
uv run ops status AlphaWbaiReversal
uv run ops status -u wbai
uv run ops status -u wbai --status submitted
```

### 7.3 未通过处理

未通过因子 src 归档在 `alpha_src/`(状态靠 state 区分),可重新提交:

```bash
# 原代码重跑 check(从 alpha_src 召回到 staging)
uv run ops recheck AlphaWbaiReversal -s rejected

# 改了代码从 dropbox 重新提交(version += 1)
uv run ops submit -u wbai -s 20260401 -f AlphaWbaiReversal --overwrite
```

## 八、日常工作要求

### 8.1 日报与周会

- **日报**: 入职首月每日总结，发送至 yong@graceim.ai 和 qi@graceim.ai
- **周会**: 分别与 @王勇、@刘琦 预约每周会议

### 8.2 联系方式

如有疑问，联系 @白文博 (wenbo@graceim.ai)

## 九、常见问题

### 9.1 数据源依赖

**Q**: 如何确定因子使用了哪些数据源？

**A**: 不要信任 XML `<Data>` 声明，实际数据源需解析 Python 代码中的 `dr.getData()` 调用。ops 会自动解析。

### 9.2 Checkpoint 失败

**Q**: CheckPoint 阶段失败怎么办？

**A**: 检查因子是否使用了跨日状态变量（如 `self.prev`），如果有，必须实现 `checkpointSave()` 和 `checkpointLoad()`。本地使用 `run_cp.py` 跑两次验证一致性。

### 9.3 相关性过高

**Q**: 相关性检测失败怎么办？

**A**:
1. 检查因子逻辑是否与现有因子过于相似
2. 尝试调整因子参数或后处理操作
3. 考虑与现有因子正交化（`AlphaOpVectorNeutralize`）

### 9.4 回测周期选择

**Q**: 应该使用完整周期还是简化周期？

**A**:
- **开发阶段**: 简化周期（20190101-20241231）快速验证
- **提交前**: 完整周期（20150101-20241231）确保稳健性

## 十、进阶主题

### 10.1 Alpha Dump vs Alpha Feature

- **alpha_dump**: 日频小文件（`yyyy/mm/yyyymmdd{v1,v2}.npy`），gsim 遗留格式
- **alpha_feature**: 聚合大文件，每个因子一个 `.npy`，推荐使用

2026-05-28 起 gsim 新增 `AlphaLoadFeat`（FeatureReader），支持从 `alpha_feature` 加载因子。详见 [gsim-changelog.md](gsim-changelog.md)。

### 10.2 因子后处理

常用 Operation（详见 [gsim-xml-config.md](gsim-xml-config.md#operations后处理链)）：

```xml
<Operations>
    <Operation module="AlphaOpPower" exp="2"/>
    <Operation module="AlphaOpDecay" days="3"/>
    <Operation module="AlphaOpRank" exp="1.0"/>
    <Operation module="AlphaOpIndNeut" group="sector"/>
</Operations>
```

可用 operator 完整列表见 [gsim-architecture.md](gsim-architecture.md#operator-模块)。

### 10.3 多种回测模式

通过 `StatsSimpleV5` 的 `mode` 参数：
- `mode=0`: Long Short
- `mode=1`: StatsBench（指数增强，需 `index_ret`）
- `mode=2`: StatsBenchLayer（分层，需 `thres`）
- `mode=3`: Long Only

各模式 XML 示例见 [gsim-xml-config.md](gsim-xml-config.md#stats-模块列表)。
