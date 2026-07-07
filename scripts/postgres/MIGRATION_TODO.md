# 三表迁移 - 非主线待办清单

> 临时文档，记录迁移主线之外的收尾事项。主线 = ops 生产库迁移。

## A. 涉及代码的（可暂缓，不影响迁移正确性）

### A1. 删除 ops health 命令
- **状态**：health 已无用，用户计划删除
- **涉及文件**：
  - `ops/services/health/health.py`（整个）
  - `ops/cli/health.py`
  - `ops/main.py` 里的 health 注册
- **连带**：health 删了之后，`ops/services/list/metrics.py` 整个变死代码（只有 health 的 refresh_metrics 在用）

### A2. 清理 derived 僵尸层
- **背景**：derived 层在新架构下已无人读（metrics/datasources/bcorr 都被 factor_snapshot 取代，index 缓存也冗余），但代码还在写它
- **涉及**：
  - `ops/services/list/list.py:228` — `scanner.scan()` 冗余调用（维护的 derived index 没人读）
  - `ops/core/library.py:104-170` — `_store()`/`_load_index_from_store()`/`_publish_index()` 走 derived 做索引缓存
  - `ops/services/list/datasource.py` — 删走 derived 的部分（`_store`/`load_datasources`/`refresh_datasources`），保留纯解析函数（`_build_npy_index`/`resolve_tables`/`parse_datasources`，submit/check 还在用）
  - `ops/services/list/metrics.py` — 随 health 一起删
  - `ops/infra/derived/` — 整个目录，最后退役
  - `ops/tools/derived_migrate.py` — 旧迁移工具，使命完成
- **注意**：`bcorr.py` 已删（本 session）

### A3. config.yaml 清理
- `derived` 配置段（指向 ops 库）在新架构下无用，可删
- 迁移完成后 `state` 配置段要从 ops_test 改回 ops

### A4. 根目录测试文件归位
- `test_end_to_end.py` / `test_all_services.py` 已移到 tests/（本 session）
- `test_new_tables.py` / `test_services.py` 已删（本 session）

---

## B. 涉及数据的（需用户决策，见下方对话）

### B1. discovery_method = NULL 的 28 个入库因子
- 详见对话展示

### B2. 20 个 hwang 孤儿的处理确认
- 详见对话展示
