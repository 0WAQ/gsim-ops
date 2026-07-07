# List & Data Sources

## Factor Data Sources

Data sources (tables and fields a factor reads via `dr.getData()`) are extracted by AST-parsing the factor `.py` and resolved to table names through an npy index. 入库时算出并存进 `factor_snapshot` 表(`infra/snapshot/`),按 `name` 键(datasources 组:fields/tables)。**入库时不可变快照**,非最新。

**Resolution pipeline** (`datasource.py`):
1. AST walk finds `*.getData(string_literal)` calls → `fields` list
2. `_build_npy_index(nio_data_path)` scans `/datasvc/data/cc/` to build `{npy_stem → table_dir}`
3. `resolve_tables(fields, index)` maps each field to its parent directory

**L2 data special case**: Directories starting with `cn_equity*` have one extra level — real `.npy` files live in `cn_equity_*/sub_table/` and the parent `cn_equity_*/` contains symlinks. The index follows symlinks only (`if npy_file.is_symlink()`) and uses the `sub_table` as the resolved table name.

## Filter Syntax (`--filter-by`)

Comma-separated `key<op>value` expressions. Comparison ops (`>`, `<`, `>=`, `<=`, `=`, `!=`) need shell quoting to avoid stdout redirect: `--filter-by "ret>30,shrp>=1.5"`.

**Supported keys**:
- `tables` — glob match (fnmatch) against any factor table, e.g. `tables=ashare*`
- `field` — exact match against any factor field
- `ret`, `shrp`, `mdd`, `tvr`, `fitness` — numeric comparison

Repeated keys AND together: `--filter-by "ret>20,ret<=30"`.

**SQL 下推**: `ops list` 的过滤/排序/截断在 Postgres 后端尽量下推到 SQL(`snapshot_store.list(...)` 拼 WHERE/ORDER BY/LIMIT),只把需要的行拉回内存。下推纯为预筛,内存侧 (`apply_filters()` / 兜底 `sort` / `[:n]`) 仍全量兜底,故结果与不下推逐位等价。

`list` 的**联合读收拢到 `ops/infra/query.py:query_factors`**,读 `factor_info` + `factor_state` + `factor_snapshot` 三表,返回 `FactorRow = (info, status, last_fail_stage, snapshot)`。行访问 `x.info.author` / `x.snapshot.ret` / `x.status`。**当前实现是三次独立查询(info.list + state.list + snapshot.list)+ 内存按 name 合并**(TODO:优化为单条 SQL LEFT JOIN,见 `query.py` 注释)。只支持 Postgres 后端。

- **`field=` / `tables=` (datasource 反查)**: `field` 走 `fields @> jsonb` 命中 `factor_snapshot` 的 GIN 索引;`tables` glob 转 LIKE 走 `EXISTS(jsonb_array_elements_text)`(含 `[]` 字符类等 LIKE 无法表达时跳过下推)。
- **metrics 阈值 (`ret>30` 等)**: 下推成 `WHERE <expr> <op> %s`,`bcorr` → `abs(max_bcorr)`。（`dump_days` 已从 filter/sort 键移除 —— 它是实时物理状态,不在 snapshot。）
- **`--status`**: 来自 `factor_state`。当前经 `state_store.list(status=...)` 单表过滤后在内存与 info/snapshot 合并(非 SQL JOIN)。
- **`--sort-by`**: 在 snapshot 查询里下推 `ORDER BY <expr> DESC NULLS LAST`;list.py 最终按 `name` stable 排序对齐。
- **factor 集合(2026-07-07 Wave 2 收敛,JOURNAL V1)**: 库内因子 = `factor_state.status != 'submitted'`(在 `query_factors` 定义,PG 唯一权威,零扫盘)。原扫盘白名单 + derived 索引缓存路径删除。PG 与磁盘漂移属对账问题(未来 ops doctor)。
- **JSON 输出变更(同批)**: `has_pnl`/`dump_days` 键移除(实时物理事实,唯一来源是全库扫盘;单因子看 `ops info`),新增 `status` 键。
- **`-n` limit 不再下推**(P0-5 修复): 旧实现无 ORDER BY 下推 LIMIT → 任意 N 行;现由内存合并后 `[:n]` 截断。

**Validation**: unknown keys, invalid syntax, and empty expressions print an error and exit early (no output). Regex was considered but deferred — glob covers the common case.
