import ast
import json
import hashlib
import time
from datetime import datetime
from pathlib import Path

from ops.core.library import FactorInfo
from ops.infra.config import Config
from ops.infra.cache import cache_path

DATASOURCES_VERSION = 1


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _get_datasources_path(config_path: Path) -> Path:
    legacy_hash = hashlib.md5(str(config_path.resolve()).encode()).hexdigest()[:8]
    library_id = Config.load(config_path).library_id
    return cache_path(library_id, "datasources.json", legacy_hash=legacy_hash)


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
    path = _get_datasources_path(config_path)
    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as f:
            data: dict = json.load(f)

        if data.get("version") != DATASOURCES_VERSION:
            return {}

        return data.get("datasources", {})
    except Exception:
        return {}


def _save_datasources(config_path: Path, datasources: dict[str, dict]) -> None:
    path = _get_datasources_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = _now_iso()
    stamped = {
        name: {**entry, "updated_at": entry.get("updated_at") or now}
        for name, entry in datasources.items()
    }
    data = {
        "version": DATASOURCES_VERSION,
        "created_at": time.time(),
        "datasources": stamped,
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def refresh_datasources(
    factors: list[FactorInfo], config: Config, config_path: Path
) -> dict[str, dict]:
    npy_index = _build_npy_index(config.nio_data_path)
    datasources: dict[str, dict] = {}

    for factor in factors:
        py_files = list(factor.src_path.glob("*.py"))
        if not py_files:
            continue
        fields = parse_datasources(py_files[0])
        tables = resolve_tables(fields, npy_index)
        datasources[factor.name] = {"fields": fields, "tables": tables}

    _save_datasources(config_path, datasources)
    return datasources


def merge_datasources(
    factors: list[FactorInfo], datasources: dict[str, dict]
) -> list[FactorInfo]:
    for factor in factors:
        factor.datasources = datasources.get(factor.name)
    return factors
