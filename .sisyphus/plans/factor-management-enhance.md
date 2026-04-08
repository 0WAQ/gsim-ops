# Factor Management Enhancement Plan

## TL;DR

> **Quick Summary**: 增强因子管理能力：数据源解析、PNL 指标提取、完整性检查
> 
> **Deliverables**:
> - 数据源解析器：从 Python 代码提取 `dr.getData()` 调用
> - PNL 指标提取：通过 `simsummary` 从 PNL 文件获取 ret/shrp/dd/fitness
> - `ops info` 增强：显示数据源和 PNL 指标
> - `ops health`：因子库完整性检查
> 
> **Estimated Effort**: Medium
> **Parallel Execution**: YES - 3 waves

---

## Context

### 现有代码基础
- `LibraryScanner` (`ops/common/library.py`) - 因子库扫描，已有缓存
- `Runner.run_simsummary()` (`ops/common/runner.py`) - 已有 PNL 解析逻辑
- `Metrics` (`ops/common/metrics.py`) - ret/shrp/tvr/fitness 数据类
- `AlphaMetadata` (`ops/common/alpha/metadata.py`) - XML 解析

### 关键约束
- **数据源**：不信任 XML `<Data>` 声明，必须解析 Python 代码中的 `dr.getData()` 调用
- **PNL 指标**：不信任 Readme.txt，必须从 PNL 文件通过 `simsummary` 获取
- **getData 模式复杂**：用户可能用 for 循环、f-string 拼接等方式调用

### getData 调用模式（从实际代码观察）
1. 简单直接：`dr.getData('ashareeodprices.s_dq_close')`
2. 变量引用：`self.volume = dr.getData('ashareeodprices.s_dq_volume').data`
3. 特殊数据：`dr.getData('cap')`, `dr.getData('status')`, `dr.getData('st')`
4. 可能的动态拼接：`dr.getData(f'equ_fancy_factors_table{i}.xxx')` (for 循环)

---

## Work Objectives

### Core Objective
增强因子管理能力，为每个因子提供数据源追踪、性能指标和完整性检查

### Concrete Deliverables
- `ops/common/datasource.py` - 数据源解析器
- `ops/common/metrics.py` - 增强 Metrics，支持从 PNL 文件提取
- `ops/common/library.py` - FactorInfo 增加 data_sources 和 metrics 字段
- `ops/info/info.py` - 增强显示数据源和 PNL 指标
- `ops/health/` - 新增 health 子命令
- `ops/main.py` - 注册 health 命令

### Must NOT Have
- 不从 Readme.txt 解析任何指标
- 不信任 XML `<Data>` 声明作为数据源
- 不修改现有 check 流水线逻辑

---

## Execution Strategy

### Wave 1 (Foundation - 并行)

#### Task 1: 数据源解析器 (`ops/common/datasource.py`)

**What to do**:
- 创建 `parse_data_sources(py_file: Path) -> list[str]` 函数
- 正则匹配 `dr.getData('xxx')` 和 `dr.getData("xxx")` 提取数据源
- 处理 `.data` 后缀：`dr.getData('xxx').data` → 提取 `xxx`
- 返回去重排序的数据源列表（如 `['ashareeodprices.s_dq_close', 'AShareMoneyFlow.sell_value_large_order']`）
- 对于动态拼接（如 f-string），尽力提取静态部分，无法解析的标记为 `<dynamic>`

**正则策略**:
```python
# 匹配所有 dr.getData / DataRegistry.getData 调用
# 模式1: 字符串字面量 dr.getData('xxx') 或 dr.getData("xxx")
# 模式2: f-string dr.getData(f'xxx{var}yyy') → 提取静态部分
```

**References**:
- `AlphaFguo12_2.py` - 简单模式：`dr.getData('ashareeodprices.s_dq_volume')`
- `AlphaJzhang20260319GA021.py` - 复杂模式：多数据源，`dr.getData('cap')`, `dr.getData('AShareMoneyFlow.sell_value_large_order')`

#### Task 2: PNL 指标提取增强 (`ops/common/metrics.py`)

**What to do**:
- 增强 `Metrics` 类，添加 `dd`（最大回撤）字段
- 添加 `Metrics.from_pnl(pnl_file: Path, config: Config) -> Metrics | None` 类方法
- 复用 `Runner.run_simsummary()` 逻辑，但作为 Metrics 的静态方法
- 添加 `to_dict()` 和 `from_dict()` 方法（用于缓存序列化）

**References**:
- `ops/common/runner.py:62-94` - 现有 simsummary 解析逻辑
- `ops/common/metrics.py` - 现有 Metrics 类
- PNL 文件格式：`date pnl long short ret ... shrp ... dd ...`

### Wave 2 (Integration - 并行)

#### Task 3: LibraryScanner 增强

**What to do**:
- `FactorInfo` 添加 `data_sources: list[str]` 和 `metrics: Metrics | None` 字段
- `_scan_directory()` 中调用 `parse_data_sources()` 解析每个因子的 .py 文件
- `_scan_directory()` 中调用 `Metrics.from_pnl()` 获取 PNL 指标
- 更新 `to_dict()` / `from_dict()` 支持新字段序列化
- 更新索引缓存版本号 `INDEX_VERSION = 2`

**References**:
- `ops/common/library.py` - 现有 LibraryScanner

#### Task 4: `ops info` 增强

**What to do**:
- 显示数据源列表
- 显示 PNL 指标（ret%, shrp, dd%, tvr%, fitness）
- 格式化输出

**输出示例**:
```
────────────────────────────────────────────────────────────
 Factor: AlphaFguo12_2
────────────────────────────────────────────────────────────
  Author:      fguo
  Src Path:    /mnt/storage/alphalib/alpha_src/AlphaFguo12_2
  Dump Path:   /mnt/storage/alphalib/alpha_dump/AlphaFguo12_2
  Dump Days:   2674
  Has PNL:     Yes

  Performance:
    Return:    10.68%
    Sharpe:    2.95
    Drawdown:  3.31%
    Turnover:  131.54%
    Fitness:   0.84

  Data Sources:
    - ashareeodprices.s_dq_volume
────────────────────────────────────────────────────────────
```

**References**:
- `ops/info/info.py` - 现有 info 实现

#### Task 5: `ops list` 增强

**What to do**:
- 表格增加 Sharpe 列
- 支持 `--sort` 参数（name, author, shrp, ret, dump_days）
- 支持 `--category` 筛选（从 XML Description 提取）

**References**:
- `ops/list/list.py` - 现有 list 实现
- `ops/list/args.py` - 现有参数定义

### Wave 3 (Health Check)

#### Task 6: `ops health` 命令

**What to do**:
- 创建 `ops/health/` 模块（`__init__.py`, `args.py`, `health.py`）
- 检查项：
  1. **孤立因子**：alpha_src 有但 alpha_dump 缺失
  2. **Dump 空洞**：alpha_dump 中日期不连续（跳过非交易日）
  3. **PNL 缺失**：alpha_src 有但 alpha_pnl 缺失
  4. **源码缺失**：alpha_dump 有但 alpha_src 缺失
  5. **文件完整性**：因子目录缺少 .py / .xml 文件
- 输出格式：按严重程度分类（ERROR / WARNING / OK）
- 注册到 `main.py`

**输出示例**:
```
Factor Library Health Check
────────────────────────────────────────────────────────────
✅ 7 factors in alpha_src
✅ 7 factors in alpha_dump
⚠️  2 factors missing PNL files:
   - AlphaXxx
   - AlphaYyy
❌ 1 factor has dump date gaps:
   - AlphaZzz: missing 20230315, 20230316
────────────────────────────────────────────────────────────
Summary: 7 OK | 2 WARNING | 1 ERROR
```

**References**:
- `ops/common/library.py` - LibraryScanner
- `ops/check/args.py` - subparser 注册模式
- `/datasvc/data/cc/__universe/Dates.npy` - 交易日历（用于判断 dump 空洞）

#### Task 7: 注册 health 命令到 main.py

**What to do**:
- 导入 `add_health_subparser`
- 注册到 subparsers

---

## Commit Strategy

| Wave | Commit Message | Files |
|------|---------------|-------|
| 1 | `feat(common): add data source parser and enhance Metrics` | `ops/common/datasource.py`, `ops/common/metrics.py` |
| 2 | `feat(list,info): enhance with data sources and PNL metrics` | `ops/common/library.py`, `ops/info/info.py`, `ops/list/list.py`, `ops/list/args.py` |
| 3 | `feat(health): add factor library health check command` | `ops/health/*`, `ops/main.py` |

---

## Success Criteria

### Verification Commands
```bash
uv run ops info AlphaJzhang20260324GA002   # 显示数据源和 PNL 指标
uv run ops list --sort shrp                # 按夏普排序
uv run ops health                          # 完整性检查报告
```

### Final Checklist
- [ ] 数据源解析覆盖简单和复杂模式
- [ ] PNL 指标从 simsummary 获取，不从 Readme
- [ ] 索引缓存包含新字段
- [ ] health 命令检测所有完整性问题
- [ ] 所有命令错误处理完善
