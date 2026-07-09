"""数据源解析(AST + npy 索引)。

submit/check/backfill 共用的领域能力(⚠ 住在 list 包下是历史遗留,迁往共享
模块属 Wave 4;full-review 第三部分 L 表)。2026-07-07 Wave 2:derived 层删除,
本文件的 refresh_datasources/load_datasources/_store 写读僵尸表的半边随之删除,
只留纯解析函数 —— datasources 的唯一落库点是 check archive 时的 factor_snapshot。
"""
import ast
from pathlib import Path


def _build_npy_index(nio_data_path: Path) -> dict[str, str]:
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
