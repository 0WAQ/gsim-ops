# Status

查询因子生命周期状态。

## 两种模式

1. **单因子详情** (`ops status AlphaXxx`): 输出完整 FactorRecord (name, status, submitted_at, entered_at, rejected_at, updated_at, last_fail, check_history) + author。**author 从 `factor_info` 读**(2026-07-06 起 FactorRecord 不含 author)。
2. **列表模式** (`ops status -u wbai --status submitted`): 按 author / status 过滤，表格输出 name + status + author + updated_at。author 过滤走 `info_store.list(author=...)`。

## 数据源

- `default_store(config)` — FactorRecord(状态,按 `state_backend` 分发,生产为 PostgresStateStore)
- `default_info_store(config)` — author(从 `factor_info` 表,FactorRecord 已无此字段)

不涉及文件系统扫描。
