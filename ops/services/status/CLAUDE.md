# Status

查询因子生命周期状态。**零展示**(2026-07-11 上收):本包只有
`query_one` / `query_many` / `query_events`(v2b,factor_history 时间线)三个
数据函数;详情/表格渲染在 `ops/cli/status.py`(C9 契约)。

## 两种模式(cli 按 args.name 路由)

1. **单因子详情** (`ops status AlphaXxx`): `query_one` → `repo.get(name)` 组全景(含 last_fail 派生切面),`query_events` → `repo.history(name)` 生命周期时间线 —— cli 渲染基础字段 + **完整操作时间线**(submit/check/entered/approve/... 含 actor,v2b;json dev/test 后端无事件表,回落 check_history 渲染)。None = 未找到;`Factor.state is None` = info 孤儿(cli 显式提示"需对账")。
2. **列表模式** (`ops status -u wbai --status submitted`): `query_many` → `repo.find(author=..., status=..., include_submitted=True)` 单条三表 JOIN —— status 的语义是"任何记录",缺省全状态,显式 `--status` 按其精确过滤。cli 表格输出 name + status + author + updated_at。无 state 的 info 孤儿行显式渲染"需对账",不静默丢。

## 数据源

`FactorRepository`(`ops/infra/repository.py`)—— 2026-07-09 阶段 3 塌缩:单因子 `repo.get`、列表 `repo.find`,退役原 `store.list` + `info_store.list` 的内存合并,不再直连 store / info_store。

不涉及文件系统扫描。
