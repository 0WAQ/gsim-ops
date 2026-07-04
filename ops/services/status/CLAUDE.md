# Status

查询因子生命周期状态。

## 两种模式

1. **单因子详情** (`ops status AlphaXxx`): 输出完整 FactorRecord — name, author, status, submitted_at/by, entered_at, rejected_at, updated_at, last_fail, check_history
2. **列表模式** (`ops status -u wbai --status submitted`): 按 author / status 过滤，表格输出 name + status + author + updated_at

## 数据源

直接读 `default_store(config)`(按 `state_backend` 分发,生产为 PostgresStateStore),不涉及文件系统扫描。
