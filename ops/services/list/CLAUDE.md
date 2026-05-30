# List & Data Sources

## Factor Data Sources

Data sources (tables and fields a factor reads via `dr.getData()`) are extracted by AST-parsing the factor `.py` and resolved to table names through an npy index. Cached at `~/.cache/ops/{hash}.datasources.json`.

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

**Validation**: unknown keys, invalid syntax, and empty expressions print an error and exit early (no output). Regex was considered but deferred — glob covers the common case.
