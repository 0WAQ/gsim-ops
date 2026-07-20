# List & Data Sources

## Factor Data Sources

Data sources (tables and fields a factor reads via `dr.getData()`) are extracted by AST-parsing the factor `.py` and resolved to table names through an npy index. 入库时算出并存进 `factor_snapshot` 表(`infra/snapshot/`),按 `name` 键(datasources 组:fields/tables)。**入库时不可变快照**,非最新。

**Resolution pipeline**(`ops/core/datasource.py`,2026-07-09 自本包迁入 —— submit/check 共用的领域纯函数):
1. AST walk finds `*.getData(string_literal)` calls → `fields` list
2. `build_npy_index(nio_data_path)` scans `/datasvc/data/cc/` to build `{npy_stem → table_dir}`
3. `resolve_tables(fields, index)` maps each field to its parent directory

**L2 data special case**: Directories starting with `cn_equity*` have one extra level — real `.npy` files live in `cn_equity_*/sub_table/` and the parent `cn_equity_*/` contains symlinks. The index follows symlinks only (`if npy_file.is_symlink()`) and uses the `sub_table` as the resolved table name.

## Filter Syntax (`--filter-by`)

Comma-separated `key<op>value` expressions. Comparison ops (`>`, `<`, `>=`, `<=`, `=`, `!=`) need shell quoting to avoid stdout redirect: `--filter-by "ret>30,shrp>=1.5"`.

**Supported keys**:
- `tables` — glob match (fnmatch) against any factor table, e.g. `tables=ashare*`
- `field` — exact match against any factor field
- `ret`, `shrp`, `mdd`, `tvr`, `fitness`, `bcorr`(abs 语义), `delay` — numeric comparison
  (键集与语义的唯一注册表 = `core/metrics.py::SNAPSHOT_METRICS`,新增键须在彼加行)

Repeated keys AND together: `--filter-by "ret>20,ret<=30"`.

**SQL 下推**: `ops list` 的过滤/排序在 Postgres 后端尽量下推到 SQL(`repo.find` 拼 WHERE/ORDER BY,snapshot 侧条件经 `snapshot_where`/`metric_order_expr` 复用单表 list 的表达式),只把需要的行拉回内存。下推纯为预筛,内存侧 (`apply_filters()` / 兜底 `sort` / `[:n]`) 仍全量兜底,故结果与不下推逐位等价。

`list` 的**联合读收拢到 `FactorRepository.find`**(`ops/infra/repository.py`,2026-07-09 退役 `query_factors` 的三次查 + 内存合并):**单条 SQL 三表 LEFT JOIN**(`factor_info` + `factor_state` + `factor_snapshot`),返回 `Factor` 聚合(`ops/core/factor.py`)。行访问 `x.identity.author` / `x.snapshot.ret` / `x.status`。只支持 Postgres 后端。

- **`field=` / `tables=` (datasource 反查)**: `field` 走 `fields @> jsonb` 命中 `factor_snapshot` 的 GIN 索引;`tables` glob 转 LIKE 走 `EXISTS(jsonb_array_elements_text)`(含 `[]` 字符类等 LIKE 无法表达时跳过下推)。
- **metrics 阈值 (`ret>30` 等)**: 下推成 `WHERE <expr> <op> %s`,`bcorr` → `abs(max_bcorr)`。键集与取值语义的正主是 `ops/core/metrics.py::SNAPSHOT_METRICS` 注册表(2026-07-11 S8 收敛:SQL 表达式、list 内存兜底 `metric_value`、CLI `--sort-by` choices 三方派生,`_SORTABLE_KEYS` 亦由其生成)。（`dump_days` 已从 filter/sort 键移除 —— 它是实时物理状态,不在 snapshot。）
- **`--status`**: 下推进 JOIN 的 WHERE(`s.status = %s`);list.py 内存侧仍兜底过滤一遍。
- **`--sort-by`**: 下推 `ORDER BY <expr> DESC NULLS LAST`(name 兜底稳定);list.py 最终按 `name` stable 排序对齐。
- **factor 集合(2026-07-07 Wave 2 收敛,JOURNAL V1)**: 库内因子 = `factor_state.status != 'submitted'`(在 `repo.find` 定义,PG 唯一权威,零扫盘)。原扫盘白名单 + derived 索引缓存路径删除。PG 与磁盘漂移属对账问题(未来 ops doctor)。
- **JSON 输出变更(同批)**: `has_pnl`/`dump_days` 键移除(实时物理事实,唯一来源是全库扫盘;单因子看 `ops info`),新增 `status` 键。
- **`-n` limit 不下推**(P0-5 语义:先滤后截): list.py 不给 `find` 传 limit,由内存过滤后 `[:n]` 截断(`find` 的 limit 参数仅显式给定时下推)。

**Validation**: unknown keys, invalid syntax, and empty expressions raise `FilterError`
(collects all errors as plain-text messages); cli prints them red and exits early
(no output, exit 0 保持旧行为). Regex was considered but deferred — glob covers the common case.

## 展示层(2026-07-11 上收)

本包**零展示**:`list_factors(args) -> list[Factor]` 只做解析/下推/内存兜底。
表格(rich Table)/JSON 渲染在 `ops/cli/list.py`(C9 契约钉住:services 不得
直引 rich);过滤错误经 `FilterError.errors` 传给 cli 呈现。

**status 列**(2026-07-13 legacy 清理批):结果集含被拒因子时,表格在 author
后显式插入 `status` 列(与 `fail_stage` 列同触发)—— ACTIVE/REJECTED 混排
只靠行颜色区分不够(颜色重定向到文件/管道即丢,且 v3 后被拒因子也有指标)。
纯 ACTIVE 列表不加此列避免噪音;JSON 输出本就有 `status` 键。
