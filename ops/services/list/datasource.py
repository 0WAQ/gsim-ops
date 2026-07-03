import ast
from pathlib import Path

from ops.core.library import FactorInfo
from ops.infra.config import Config
from ops.infra.derived import default_derived_store


def _store(config_path: Path):
    return default_derived_store(Config.load(config_path))


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


def load_datasources(config_path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for name, rec in _store(config_path).get_all().items():
        if rec.fields is None and rec.tables is None:
            continue
        out[name] = {"fields": rec.fields or [], "tables": rec.tables or []}
    return out


def refresh_datasources(
    factors: list[FactorInfo], config: Config, config_path: Path
) -> dict[str, dict]:
    npy_index = _build_npy_index(config.nio_data_path)
    store = _store(config_path)

    for factor in factors:
        py_files = list(factor.src_path.glob("*.py"))
        if not py_files:
            continue
        fields = parse_datasources(py_files[0])
        tables = resolve_tables(fields, npy_index)
        store.upsert_datasources(factor.name, fields, tables)

    return load_datasources(config_path)


def merge_datasources(
    factors: list[FactorInfo], datasources: dict[str, dict]
) -> list[FactorInfo]:
    for factor in factors:
        factor.datasources = datasources.get(factor.name)
    return factors
