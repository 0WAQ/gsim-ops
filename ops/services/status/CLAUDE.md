# Status

查询因子生命周期状态。**零展示**(2026-07-11 上收):本包只有
`query_one(args) -> Factor | None` / `query_many(args) -> list[Factor]` 两个
数据函数;详情/表格渲染在 `ops/cli/status.py`(C9 契约)。

## 两种模式(cli 按 args.name 路由)

1. **单因子详情** (`ops status AlphaXxx`): `query_one` → `repo.get(name)` 组全景,cli 输出完整 FactorRecord (name, status, submitted_at, entered_at, rejected_at, updated_at, last_fail, check_history 全史) + author(自 `Factor.identity`,2026-07-06 起 FactorRecord 不含 author)。None = 未找到;`Factor.state is None` = info 孤儿(cli 显式提示"需对账")。
2. **列表模式** (`ops status -u wbai --status submitted`): `query_many` → `repo.find(author=..., status=..., include_submitted=True)` 单条三表 JOIN —— status 的语义是"任何记录",缺省全状态,显式 `--status` 按其精确过滤。cli 表格输出 name + status + author + updated_at。无 state 的 info 孤儿行显式渲染"需对账",不静默丢。

## 数据源

`FactorRepository`(`ops/infra/repository.py`)—— 2026-07-09 阶段 3 塌缩:单因子 `repo.get`、列表 `repo.find`,退役原 `store.list` + `info_store.list` 的内存合并,不再直连 store / info_store。

不涉及文件系统扫描。
