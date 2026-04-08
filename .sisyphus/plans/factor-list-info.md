# Factor List & Info Commands

## TL;DR

> **Quick Summary**: 实现 `ops list` 和 `ops info` 命令，用于查询因子库中的因子列表和详情
> 
> **Deliverables**:
> - `ops list` - 列出所有因子（支持按作者筛选）
> - `ops info <name>` - 显示单个因子详情
> 
> **Estimated Effort**: Short
> **Parallel Execution**: YES - 2 waves
> **Critical Path**: Task 1 → Task 2/3 → Task 4

---

## Context

### Original Request
实现因子库的基础查询功能：list 和 info 命令

### 现有代码基础
- `AlphaMetadata` 类 (`ops/common/alpha/metadata.py`) - 已有因子元数据解析
- `AlphaKey` 类 (`ops/common/alpha/key.py`) - user/date/name 数据结构
- 因子库路径: `/mnt/storage/alphalib/alpha_src/`
- 参考 `ops/check/args.py` 的 subparser 注册模式

---

## Work Objectives

### Core Objective
为因子库添加查询能力，方便用户了解库中有哪些因子及其详细信息

### Concrete Deliverables
- `ops/list/` 模块 - list 命令实现
- `ops/info/` 模块 - info 命令实现
- `main.py` 更新 - 注册新命令

### Definition of Done
- [ ] `uv run ops list` 显示所有因子
- [ ] `uv run ops list -u wbai` 按作者筛选
- [ ] `uv run ops info AlphaXxx` 显示因子详情

### Must Have
- 扫描 `alpha_src/` 目录获取因子列表
- 表格格式输出（使用 colorama）
- 错误处理（因子不存在等）

### Must NOT Have
- 不修改现有 AlphaMetadata 类的核心逻辑
- 不引入新的外部依赖

---

## Verification Strategy

### Test Decision
- **Infrastructure exists**: NO
- **Automated tests**: None
- **Framework**: none

### QA Policy
手动验证命令输出

---

## Execution Strategy

### Parallel Execution Waves

```
Wave 1 (Start Immediately - 基础模块):
├── Task 1: 创建 LibraryScanner 工具类 [quick]

Wave 2 (After Wave 1 - 命令实现, PARALLEL):
├── Task 2: 实现 ops list 命令 [quick]
├── Task 3: 实现 ops info 命令 [quick]

Wave 3 (After Wave 2 - 集成):
├── Task 4: 注册命令到 main.py + 测试 [quick]
```

---

## TODOs

- [ ] 1. 创建 LibraryScanner 工具类

  **What to do**:
  - 在 `ops/common/` 下创建 `library.py`
  - 实现 `LibraryScanner` 类，扫描 `alpha_src/` 目录
  - 返回因子列表，每个因子包含: name, author (从名称解析), src_path, dump_path, has_pnl

  **Must NOT do**:
  - 不修改现有 AlphaMetadata 类

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (基础依赖)
  - **Blocks**: Task 2, Task 3
  - **Blocked By**: None

  **References**:
  - `ops/common/alpha/metadata.py` - 参考因子元数据结构
  - `config.yaml` - alpha_src 路径配置
  - `ops/common/config.py` - Config 类加载方式

  **Acceptance Criteria**:
  - [ ] `LibraryScanner.scan()` 返回因子列表
  - [ ] 每个因子有 name, author, paths 信息

  **Commit**: YES
  - Message: `feat(common): add LibraryScanner for factor library`
  - Files: `ops/common/library.py`

---

- [ ] 2. 实现 ops list 命令

  **What to do**:
  - 创建 `ops/list/` 目录，包含 `__init__.py`, `args.py`, `list.py`
  - 实现表格输出: Name | Author | Dump Days | Has PNL
  - 支持 `--user/-u` 筛选
  - 支持 `--format` 选项 (table/json)

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (与 Task 3 并行)
  - **Parallel Group**: Wave 2
  - **Blocks**: Task 4
  - **Blocked By**: Task 1

  **References**:
  - `ops/check/args.py` - subparser 注册模式
  - `ops/common/logger/log.py` - 输出格式参考
  - 使用 colorama 做表格输出

  **Acceptance Criteria**:
  - [ ] `ops list` 输出因子表格
  - [ ] `ops list -u wbai` 筛选有效

  **Commit**: YES
  - Message: `feat(list): add ops list command`
  - Files: `ops/list/__init__.py`, `ops/list/args.py`, `ops/list/list.py`

---

- [ ] 3. 实现 ops info 命令

  **What to do**:
  - 创建 `ops/info/` 目录，包含 `__init__.py`, `args.py`, `info.py`
  - 显示因子详情:
    - 基本信息: name, author, create_date
    - 路径: src_path, dump_path, pnl_path
    - 数据统计: dump 天数, 日期范围
  - 因子不存在时友好提示

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: YES (与 Task 2 并行)
  - **Parallel Group**: Wave 2
  - **Blocks**: Task 4
  - **Blocked By**: Task 1

  **References**:
  - `ops/check/args.py` - subparser 注册模式
  - `ops/common/alpha/metadata.py:get_v2npy_files()` - 获取 dump 文件列表

  **Acceptance Criteria**:
  - [ ] `ops info AlphaXxx` 显示详情
  - [ ] 因子不存在时提示错误

  **Commit**: YES
  - Message: `feat(info): add ops info command`
  - Files: `ops/info/__init__.py`, `ops/info/args.py`, `ops/info/info.py`

---

- [ ] 4. 注册命令到 main.py

  **What to do**:
  - 在 `main.py` 中导入并注册 list 和 info 子命令
  - 端到端测试两个命令

  **Recommended Agent Profile**:
  - **Category**: `quick`
  - **Skills**: []

  **Parallelization**:
  - **Can Run In Parallel**: NO (最终集成)
  - **Blocks**: None
  - **Blocked By**: Task 2, Task 3

  **References**:
  - `ops/main.py` - 现有命令注册方式

  **Acceptance Criteria**:
  - [ ] `uv run ops --help` 显示 list 和 info
  - [ ] 两个命令正常工作

  **Commit**: YES
  - Message: `feat(main): register list and info commands`
  - Files: `ops/main.py`

---

## Commit Strategy

| Task | Commit Message | Files |
|------|---------------|-------|
| 1 | `feat(common): add LibraryScanner for factor library` | `ops/common/library.py` |
| 2 | `feat(list): add ops list command` | `ops/list/*` |
| 3 | `feat(info): add ops info command` | `ops/info/*` |
| 4 | `feat(main): register list and info commands` | `ops/main.py` |

---

## Success Criteria

### Verification Commands
```bash
uv run ops --help           # 显示 list, info 子命令
uv run ops list             # 显示因子表格
uv run ops list -u wbai     # 筛选 wbai 的因子
uv run ops info AlphaXxx    # 显示因子详情
```

### Final Checklist
- [ ] list 命令正常工作
- [ ] info 命令正常工作
- [ ] 错误处理完善
- [ ] 输出格式清晰
