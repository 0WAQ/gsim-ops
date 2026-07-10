"""数据源解析 —— submit/check/backfill 共用的领域能力(纯函数)。

2026-07-09 自 `ops/services/list/datasource.py` 迁入(factor-aggregate-plan 阶段 2):
原先住在 list 包下是历史遗留,submit/backfill/check 跨包借用构成 4 条 C3 违例边
(service 包相互独立契约)。datasources 的唯一落库点是 check archive 时的
factor_snapshot(入库时不可变快照)。

- `parse_datasources`:AST 走查因子 .py 里的 `*.getData("xxx")` 字面量 → fields
  (XML `<Data>` 声明不可信,以代码实际调用为准,见根 CLAUDE.md)。
- `build_npy_index`:扫 nio_data_path(/datasvc/data/cc/)建 {npy_stem → 表名}
  索引。L2 特例:`cn_equity*` 目录多一层,真 .npy 在子目录、父目录放软链,
  索引只认软链并以子目录名为表名。
- `resolve_tables`:fields 经索引映射为表名集合。
"""
import ast
from pathlib import Path


def build_npy_index(nio_data_path: Path) -> dict[str, str]:
    index: dict[str, str] = {}
    if not nio_data_path.exists():
        return index
    for table_dir in nio_data_path.iterdir():
        if not table_dir.is_dir():
            continue
        if table_dir.name.startswith("cn_equity"):
            for sub_dir in table_dir.iterdir():
                if not sub_dir.is_dir():
                    continue
                for npy_file in sub_dir.glob("*.npy"):
                    if npy_file.is_symlink():
                        index[npy_file.stem] = sub_dir.name
        else:
            for npy_file in table_dir.glob("*.npy"):
                index[npy_file.stem] = table_dir.name
    return index


def parse_datasources(py_file: Path) -> list[str]:
    try:
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except Exception:
        return []

    fields: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "getData":
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                fields.add(node.args[0].value)

    return sorted(fields)


def resolve_tables(fields: list[str], npy_index: dict[str, str]) -> list[str]:
    tables: set[str] = set()
    for field in fields:
        table = npy_index.get(field)
        if table:
            tables.add(table)
    return sorted(tables)
