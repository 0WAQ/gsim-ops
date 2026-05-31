# Gsim 因子入库检测流程

本文档说明因子入库前的验证流程和标准。

## 检测流程概览

因子提交后，通过 `ops check` 进入 7 阶段验证管道（实际实现见 `ops/services/check/`）：

```
提交 → Validate → Checkbias → Checkpoint → Long Backtest → Compliance → Correlation → Archive
```

回测区间：
- **极短回测**: 20241201 - 20241202（Validate，仅 2 个交易日，验证可运行）
- **短回测**: 20241201 - 20241231（Checkbias、Checkpoint）
- **长回测**: 20150101 - 20251231（Long Backtest 及后续指标计算）

成功因子的 alpha 和 pnl 会被归档到因子库 `/mnt/storage/alphalib/`。

## 检测阶段详解

### 0. Validate（基础验证）

**目的**: 验证因子代码和配置能否正常运行（无 DataFirewall 注入的短回测）。

**实现**: `ops/services/check/checker/validate_checker.py`

**意义**: 提早暴露配置错误、import 错误、运行时异常，避免后续阶段浪费时间。

**失败处理**: 状态回退到 `SUBMITTED`，因子留在 staging（环境/配置问题，可通过 `ops check --retry` 重试）。

### 1. Checkbias（未来数据泄露检测）

**目的**: 确保因子不使用未来数据。

**实现**: `ops/services/check/checker/checkbias_checker.py` + `firewall.py`

**机制**: 通过 AST 注入 `@DataFirewall` 装饰器到因子的 `generate()` 方法，运行时拦截对 `dr.getData()` 返回数据的访问。

**AST 分析流程**:
1. `_GetDataAttrCollector` 扫描因子 `__init__`，收集形如 `self.xxx = dr.getData(...)` 或 `.data` 的赋值
2. 收集到的属性名 + `{'valid'}` 组成 `data_attrs` 集合
3. `_GenerateDecoratorInjector` 将 `@DataFirewall(delay=X, data_attrs={...})` 注入到 `generate`

**运行时行为**: `DataFirewall` 只包装 `data_attrs` 中的属性为 `_SafeProxy`。用户自建的 buffer（`np.zeros`、`.copy()` 等）不受影响。

**禁止访问规则**:

| 因子 delay | 数据维度 | 规则 |
|-----------|---------|------|
| `>= 1` | 任意 | 不能访问 `data[di]`，只能 `data[:di]` |
| `0` | 2D `[di, ii]`（日频） | 不能访问 `data[di]`（日频数据 EOD 才可知） |
| `0` | 3D `[di, ti, ii]`（日内） | 可访问 `data[di, :44, :]`（到 14:30，ti <= 43） |

**例外**: `self.valid` 总是允许访问 `[di]`（可交易性盘前已知）。

**安全设计**:
- 装饰器写入到 `{factor}_firewall.py` 临时文件，原始 `.py` 不被改动
- XML 临时指向临时文件，`finally` 块恢复 XML 并删除临时文件
- 进程崩溃不会留下半装饰的代码

**失败处理**: 状态转为 `REJECTED`，因子移到 recycle。

### 2. Checkpoint（断点恢复检测）

**目的**: 验证因子在停止后能否恢复执行（用 `run_cp.py` 跑两遍）。

**实现**: `ops/services/check/checker/checkpoint_checker.py`

**机制**: 使用 `run_cp.py` 跑回测，配置 `checkpointDays=5`。第一遍跑到 `endIndex - 5` 时保存 checkpoint，从 checkpoint 恢复继续；第二遍直接从 checkpoint 恢复。两次最终 PNL 必须一致。

#### 问题示例

```python
class AlphaExample(AlphaBase):
    def __init__(self, cfg):
        AlphaBase.__init__(self, cfg)
        self.prev = None
        
    def generate(self, di):
        if self.prev is None:
            self.prev = alloc_some_bytes()
        else:
            self.prev = some_calculate(...)
        self.alpha = some_calculate(self.prev)
```

**问题**: `self.prev` 是跨日状态，重启后丢失 → 第二遍结果不一致。

#### 解决方案

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

#### 配置

XML 中通过 `Constants` 控制：

```xml
<Constants backdays="256" niodatapath="/datasvc/data/cc"
    checkpointDir="checkpoint" checkpointDays="5"/>
```

- `checkpointDir`: checkpoint 保存目录
- `checkpointDays`: 保存间隔天数

**本地验证**: 用户可在本地用 `run_cp.py` 跑两遍，对比 PNL 一致性。

**失败处理**: 状态转为 `REJECTED`，移到 recycle。

### 3. Long Backtest（长回测）

**目的**: 在完整历史周期上验证因子表现。

**实现**: `ops/services/check/checker/long_backtest_checker.py`

**回测周期**: 20150101 - 20251231（纯执行，不做检查）

**输出**: PNL 文件和 alpha_dump，用于后续 Compliance 和 Correlation 检测。

**失败处理**: 状态回退到 `SUBMITTED`（环境/配置问题，可重试）。

### 4. Compliance（仓位合规检测）

**目的**: 确保因子满足仓位约束。

**实现**: `ops/services/check/checker/compliance_checker.py`

**检测指标**:

| 指标 | 标准 |
|-----|------|
| 个股最大持仓比例 | ≤ 5% |
| 多头最小持股数 | ≥ 50 |
| 空头最小持股数 | ≥ 50 |
| 总最小持股数 | ≥ 100 |

**常见问题**:
- 因子值过于集中导致持股数不足
- 因子值分布不均导致多空失衡

**如何避免**:
- 使用 `AlphaOpRank` 排序，增加分散度
- 使用 `AlphaOpIndNeut` 行业中性化

**失败处理**: 状态转为 `REJECTED`，移到 recycle。

### 5. Correlation（相关性检测）

**目的**: 确保新因子与现有因子池不过度相关。

**实现**: `ops/services/check/checker/correlation_checker.py`

**标准**: 最大相关性 ≤ 0.7

**检测方法**: 使用 `/usr/local/gsim/dataops/bcorr` 计算新因子 PNL 与因子库中所有 PNL 的相关性。

**手动相关性测试**:

```bash
/usr/local/gsim/dataops/bcorr new_factor_pnl /usr/local/gsim/pnl_prod/
# 或
/usr/local/gsim/dataops/bcorr new_factor_pnl /mnt/storage/alphalib/alpha_pnl/
```

**失败处理**: 状态转为 `REJECTED`，移到 recycle。

### 6. Archive（归档）

**目的**: 将通过检测的因子归档到因子库。

**操作**:
- 运行 `simsummary` 提取指标（ret/shrp/dd/tvr/fitness）
- 保存指标到 ops 索引
- 将因子源代码、Config、Readme 移动到 `alpha_src/`
- 将 PNL 文件移动到 `alpha_pnl/`
- 将 alpha_dump 移动到 `alpha_dump/`
- 更新状态为 `ACTIVE`，生成 `meta.json`

## 失败语义总结

| 阶段 | 失败后状态 | 因子去向 |
|-----|-----------|---------|
| Validate | `SUBMITTED` | 留 staging（可 retry） |
| Checkbias | `REJECTED` | 移 recycle |
| Checkpoint | `REJECTED` | 移 recycle |
| Long Backtest | `SUBMITTED` | 留 staging（可 retry） |
| Compliance | `REJECTED` | 移 recycle |
| Correlation | `REJECTED` | 移 recycle |
| Archive | `REJECTED` | 移 recycle |

设计原则：
- **环境/配置问题** → 回退到 SUBMITTED，可 retry
- **因子质量问题** → REJECTED，QR 必须改代码重新提交

## 并发与隔离

- 使用 `ProcessPoolExecutor`（最多 20 workers）并行检测多个因子
- 每个因子操作前获取 `~/.cache/ops/locks/{name}.lock` 的非阻塞 fcntl 锁
- 锁竞争时直接跳过（不排队），避免重复检查

## 入库标准

入库阈值由 `config.yaml` 的 `correlation` 节统一控制（不区分 delay）：

| 指标 | 标准 | config 字段 |
|-----|------|-------------|
| 年化收益率 (ret%) | ≥ 10% | `correlation.ret%` |
| 换手率 (tvr%) | ≥ 40% | `correlation.tvr%` |
| 夏普比率 (shrp) | ≥ 2.00 | `correlation.shrp` |
| 最大相关性 | < 0.7 | `correlation.corr_threshold` |

> 注：当前实现未对 `delay=0` 设置更高阈值。若希望对当日交易因子施加更严格标准，需在 `config.yaml` 中扩展或在评审环节人工把关。

### 仓位约束

阈值由 `config.yaml` 的 `compliance` 节定义：

| 指标 | 默认 | config 字段 |
|-----|------|-------------|
| 个股最大持仓比例 | ≤ 5% | `compliance.max_position_pct` (0.05) |
| 多头最小持股数 | ≥ 50 | `compliance.min_long_stocks` |
| 空头最小持股数 | ≥ 50 | `compliance.min_short_stocks` |
| 总最小持股数 | ≥ 100 | `compliance.min_total_stocks` |

## Recycle 机制

未通过的因子进入 `/mnt/storage/recycle/{UnixId}/`，按淘汰环节分目录：

```
/mnt/storage/recycle/wbai/
├── checkbias/
│   └── AlphaWbaiExample1/
│       ├── AlphaWbaiExample1.py
│       ├── Config.Wbai.Example1.xml
│       ├── Readme.Wbai.Example1.txt
│       └── reason.txt
├── checkpoint/
├── compliance/
└── correlation/
```

`reason.txt` 记录具体失败原因。

### 召回处理

修改后可以重新提交：

```bash
# 从 recycle 召回 rejected 因子（重新进入 staging）
uv run ops resubmit AlphaWbaiExample1 -s rejected

# 从 dropbox 重新提交
uv run ops submit -u wbai -s 20260401 -f AlphaWbaiExample1
```

## State Reconciliation

`ops check` 启动时会运行 reconcile 修复状态漂移（进程崩溃可能在文件移动和状态变更之间死亡）：

| state | 文件位置 | 修正动作 |
|-------|---------|---------|
| SUBMITTED | staging | OK |
| SUBMITTED | alpha_src | → ACTIVE |
| SUBMITTED | recycle | → REJECTED |
| SUBMITTED | 找不到 | 删除记录 |
| CHECKING | staging | → SUBMITTED（中途崩溃） |
| CHECKING | alpha_src | → ACTIVE |
| CHECKING | recycle | → REJECTED |
| ACTIVE | 不在 alpha_src | 警告（不自动修复） |
| REJECTED | 不在 recycle | 警告 |
| DELETED | staging | → SUBMITTED（重新提交） |
| DELETED | alpha_src | → ACTIVE（tombstone 失效） |

文件系统是真相之源，reconcile 只调整状态。

## 查看检测状态

### 查看单个因子

```bash
uv run ops status AlphaWbaiExample
```

### 查看所有因子

```bash
uv run ops status -u wbai
uv run ops status -u wbai --status checking
uv run ops status -u wbai --status rejected
```

### 查看因子列表

```bash
uv run ops list -s submitted
uv run ops list -s checking
uv run ops list -s rejected
```

## 常见失败原因及解决方案

### Validate 失败

**原因**: 代码语法错、import 失败、配置错误

**解决**: 检查 `ops status` 的错误信息，在本地用 `run.py` 跑短回测复现

### Checkbias 失败

**原因**: 使用了未来数据，被 DataFirewall 拦截

**解决方案**:
1. 检查所有数据访问是否使用 `di - self.delay`
2. 检查是否在 `__init__` 中预计算了所有日期的结果
3. 注意 delay=0 的因子访问 3D 数据时 ti 上限为 43

### Checkpoint 失败

**原因**: 因子使用跨日状态变量但未实现 checkpoint

**解决方案**:
1. 识别所有跨日状态变量（如 `self.prev`、`self.buffer`）
2. 实现 `checkpointSave()` 和 `checkpointLoad()`
3. 本地用 `run_cp.py` 跑两遍对比

### Compliance 失败

**原因**: 仓位不满足约束

**解决方案**:
1. 使用 `AlphaOpRank` 增加分散度
2. 使用 `AlphaOpIndNeut` 行业中性化
3. 调整因子计算逻辑

### Correlation 失败

**原因**: 与现有因子相关性过高

**解决方案**:
1. 在提交前先用 `bcorr` 测试
2. 尝试不同的数据源组合
3. 使用 `AlphaOpVectorNeutralize` 正交化

### Long Backtest 失败

**原因**: 回测过程中出错（代码错、数据缺失、内存溢出）

**解决方案**:
1. 检查回测日志定位错误位置
2. 检查数据访问是否越界
3. 检查除零、NaN 数值问题
4. 本地用相同配置重现

## 最佳实践

### 1. 开发阶段就进行相关性测试

```bash
/usr/local/gsim/dataops/bcorr your_pnl /usr/local/gsim/pnl_prod/
```

### 2. 使用简化周期快速迭代

开发用 `20190101-20241231`，提交前用 `20150101-20241231`。

### 3. 实现 Checkpoint 占位

即使当前不使用状态变量，也建议留空 checkpoint 方法：

```python
def checkpointSave(self, fh):
    pass

def checkpointLoad(self, fh):
    pass
```

### 4. 后处理增强稳健性

```xml
<Operations>
    <Operation module="AlphaOpRank" exp="1.0"/>
    <Operation module="AlphaOpIndNeut" group="sector"/>
</Operations>
```

### 5. 本地完整验证再提交

```bash
# 1. 短回测验证可运行
/usr/local/gsim/.venv/bin/python /usr/local/gsim/run.py config.xml

# 2. checkpoint 验证
/usr/local/gsim/.venv/bin/python /usr/local/gsim/run_cp.py config.xml
/usr/local/gsim/.venv/bin/python /usr/local/gsim/run_cp.py config.xml

# 3. 相关性检测
/usr/local/gsim/dataops/bcorr pnl_file /usr/local/gsim/pnl_prod/

# 4. PNL 汇总
/usr/local/gsim/.venv/bin/python /usr/local/gsim/tools/simsummary.py pnl_file
```

## 参考资料

- Gsim 架构：[gsim-architecture.md](gsim-architecture.md)
- XML 配置：[gsim-xml-config.md](gsim-xml-config.md)
- 因子开发流程：[gsim-factor-workflow.md](gsim-factor-workflow.md)
- 数据源参考：[gsim-data-sources.md](gsim-data-sources.md)
- ops check 实现：`ops/services/check/`
