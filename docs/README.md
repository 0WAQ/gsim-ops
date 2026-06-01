# Gsim 文档

本目录包含 gsim 回测框架的技术文档。

## 文档列表

按使用频率排序：

| 文档 | 说明 | 何时阅读 |
|-----|------|---------|
| [gsim-factor-workflow.md](gsim-factor-workflow.md) | 因子开发完整流程 | 开发新因子时 |
| [gsim-xml-config.md](gsim-xml-config.md) | XML 配置详细说明 | 配置回测时 |
| [gsim-data-sources.md](gsim-data-sources.md) | gsim 框架数据源 API(XML / dr.getData) | gsim 内开发因子时 |
| [cc-data-layout.md](cc-data-layout.md) | /datasvc/data/{cc, cc_2024, cc_2025, cc_all} 物理布局 | 跳过 gsim 直接做因子挖掘 / ML 时 |
| [gsim-architecture.md](gsim-architecture.md) | 架构、模块、工具链 | 了解框架时 |
| [gsim-factor-validation.md](gsim-factor-validation.md) | 入库检测流程和标准 | 准备提交时 |
| [gsim-changelog.md](gsim-changelog.md) | Gsim 更新日志 | 跟踪新特性时 |

## 快速开始

### 路径速查

| 资源 | 路径 |
|-----|------|
| Gsim 主目录 | `/usr/local/gsim/` |
| 完整模板因子 | `/datasvc/template/AlphaWbaiReversal/` |
| 完整数据源配置 | `/datasvc/template/config.read_cache.xml` |
| 数据缓存（只读） | `/datasvc/data/cc/` |
| 因子库 | `/mnt/storage/alphalib/` |
| 提交入口 | `/mnt/storage/dropbox/{Unix ID}/` |
| 淘汰回收站 | `/mnt/storage/recycle/{Unix ID}/` |
| 生产 PNL 池 | `/usr/local/gsim/pnl_prod/` |

### 核心命令速查

```bash
# 回测
/usr/local/gsim/.venv/bin/python /usr/local/gsim/run.py config.xml

# Checkpoint 回测（验证断点恢复）
/usr/local/gsim/.venv/bin/python /usr/local/gsim/run_cp.py config.xml

# PNL 汇总
/usr/local/gsim/.venv/bin/python /usr/local/gsim/tools/simsummary.py /path/to/pnl

# 相关性测试（推荐 C++ 版）
/usr/local/gsim/dataops/bcorr pnl1 /usr/local/gsim/pnl_prod/

# ops 命令
uv run ops submit -u wbai -s 20260401   # 提交因子
uv run ops status AlphaWbaiReversal     # 查询状态
uv run ops list -u wbai                 # 列出因子
```

## 核心概念

### 因子生命周期

```
开发 → 回测 → 分析 → 评审 → 提交 → 入库检测 → 归档
```

### 数据组织

- `alpha_src/`: 因子源代码
- `alpha_pnl/`: 回测 PNL
- `alpha_dump/`: 日频小文件（逐步弃用）
- `alpha_feature/`: 聚合大文件（推荐，2026-05-28 起）

### 因子命名规范

`Alpha{UnixId}{Name}`，例如 `AlphaWbaiReversal`。

### 入库标准（速查）

阈值由 `config.yaml` 统一控制（当前不区分 delay）：

| 项目 | 标准 |
|-----|------|
| 年化收益率 (ret%) | ≥ 10% |
| 换手率 (tvr%) | ≥ 40% |
| 夏普比率 (shrp) | ≥ 2.00 |
| 最大相关性 | < 0.7 |
| 个股最大持仓 | ≤ 5% |
| 多/空最小持股数 | ≥ 50 |
| 总最小持股数 | ≥ 100 |

详见 [gsim-factor-validation.md](gsim-factor-validation.md)。

## 注意事项

1. **文档时效性**: 本套文档基于 `/usr/local/gsim` 实际代码整理，但 gsim 持续演进。实际开发时以代码为准，特别是：
   - 模块列表：`gsim/*/__init__.py`
   - XML schema：`gsim/gsim.xsd`
   - 数据字段：`source_ref/` 或编译模块源码
2. **数据源依赖**: 不要信任 XML `<Data>` 声明，实际依赖需解析 Python 中的 `dr.getData()` 调用
3. **Checkpoint**: 使用跨日状态变量的因子必须实现 `checkpointSave()` / `checkpointLoad()`
4. **相关性**: 提交前先用 `bcorr` 测试，避免被 reject

## 学习路径

### 新人入门
1. 阅读 [gsim-architecture.md](gsim-architecture.md) 了解整体架构
2. 阅读 [gsim-factor-workflow.md](gsim-factor-workflow.md) 学习开发流程
3. 直接复制 `/datasvc/template/AlphaWbaiReversal/` 作为模板
4. 修改配置和代码，本地回测验证
5. 阅读 [gsim-factor-validation.md](gsim-factor-validation.md) 准备提交

### 老手开发
1. 查 [gsim-data-sources.md](gsim-data-sources.md) 找数据
2. 查 [gsim-xml-config.md](gsim-xml-config.md) 调配置
3. 跟踪 [gsim-changelog.md](gsim-changelog.md) 了解新特性

## 联系方式

如有疑问，联系 @白文博 (wenbo@graceim.ai)
