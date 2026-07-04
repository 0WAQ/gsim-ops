# List & Data Sources

## Factor Data Sources

Data sources (tables and fields a factor reads via `dr.getData()`) are extracted by AST-parsing the factor `.py` and resolved to table names through an npy index. Stored in the DerivedStore (`infra/derived/`, Postgres or json fallback), keyed by `(library_id, name)`.

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
- `ret`, `shrp`, `mdd`, `tvr`, `fitness`, `dump_days` — numeric comparison

Repeated keys AND together: `--filter-by "ret>20,ret<=30"`.

**SQL 下推**: `ops list` 的过滤/排序/截断在 Postgres 后端尽量下推到 SQL,只把需要的行拉回内存。下推纯为预筛,内存侧 (`apply_filters()` / 兜底 `sort` / `[:n]`) 仍全量兜底,故结果与不下推逐位等价。json 回退后端在内存里镜像同语义。

- **`field=` / `tables=` (datasource 反查)**: `field` 走 `fields @> jsonb` 命中 GIN 索引 `ix_fd_fields`;`tables` glob 转 LIKE 走 `EXISTS(jsonb_array_elements_text)`(含 `[]` 字符类等 LIKE 无法表达时跳过下推)。多个同类条件只下推第一个,其余靠兜底。
- **metrics 阈值 (`ret>30` 等)**: 下推成 `WHERE <expr> <op> %s`,`bcorr` → `abs(max_bcorr)`、`dump_days` → `COALESCE(dump_days,0)`。`!=` 不下推(`apply_filters` 未实现该 op,现状静默 no-op,剔除以保持等价)。
- **`--sort-by`**: 下推成 `ORDER BY <expr> DESC NULLS LAST, name ASC`(`name` 二级序对齐内存 stable sort 的 tie-break);无 `--sort-by` 时 `ORDER BY name ASC`。
- **`-n` limit**: 只在 SQL 结果集 == 最终结果集时下推(`can_push_limit` gate:无 `--status`、无 `field=`/`tables=`);否则留 `[:n]` 内存兜底。limit 减少行数不是纯预筛,gate 防止 SQL 后的 Python 过滤把行数砍到 `< n`。
- **factor 集合**: list 恒过滤 `author IS NOT NULL`(有 index 组 == 在 alpha_src),经 `has_index=True` 下推。

数值键的取值/排序语义有单一 Python 真相源 `ops/infra/derived/base.py:metric_get` / `sort_key`(list.py 内存兜底 + json 后端复用),pg_store 的 `_METRIC_EXPR` SQL 表达式必须逐键镜像,三处不能 drift。

**Validation**: unknown keys, invalid syntax, and empty expressions print an error and exit early (no output). Regex was considered but deferred — glob covers the common case.
