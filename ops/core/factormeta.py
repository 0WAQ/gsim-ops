"""FactorMeta —— 因子的 meta.json 身份证格式 + 从因子目录解析它的领域函数。

2026-07-09(factor-aggregate-plan 阶段 2):`parse_factor` / `infer_author_from_dir`
自 `ops/services/submit/parser.py` 迁入 —— 解析"一个因子目录 → FactorMeta"是领域
能力,backfill/clear 跨包借用 submit.parser 构成 2 条 C3 违例边。归宿与 FactorMeta
本体同模块:产物与其构造器一处安放。Config 仅作类型引用(core 不运行期依赖 infra,
运行期只取 config.nio_data_path 属性)。
"""
from __future__ import annotations

import ast
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ops.core.datasource import build_npy_index, parse_datasources, resolve_tables
from ops.utils.xmlio import load_xml

if TYPE_CHECKING:
    from ops.infra.config import Config

META_VERSION = 1


@dataclass
class FactorMeta:
    name: str
    author: str
    birthday: int
    universe: str
    category: str
    delay: int

    backdays: int
    dump_alpha: bool
    has_intraday_curve: bool

    operations: list[dict] = field(default_factory=list)
    declared_data_modules: list[str] = field(default_factory=list)

    datasources: dict = field(default_factory=lambda: {"fields": [], "tables": []})
    code_lines: int = 0

    frequency: str = "daily"
    discovery_method: str | None = None

    submitted_at: str | None = None
    submitted_by: str | None = None
    meta_version: int = META_VERSION

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False))

    @classmethod
    def load(cls, path: Path) -> "FactorMeta":
        return cls(**json.loads(path.read_text()))


# ---------------------------------------------------------------------------
# 因子目录 → FactorMeta 解析(原 services/submit/parser.py,2026-07-09 迁入)
# ---------------------------------------------------------------------------

INTRADAY_HINTS = ("Interval", "intraday", "tick", "5m", "1m", "15m", "30m")
_GENERIC_AUTHORS = {"gsim_users", "unknown", ""}


def infer_author_from_dir(factor_dir_name: str) -> str:
    """AlphaFguo20260303LLM010 → fguo, AlphaWbaiReversal → wbai。

    纯词法,不识身份:目录命名规范 Alpha{User}{Xxx} 是权威来源,但
    `AlphaInterpFoo` 也会推出 `interp`(off-spec 命名落到非预期 bucket)。
    """
    name = factor_dir_name
    if name.startswith("Alpha"):
        name = name[5:]
    author = []
    for ch in name:
        if ch.islower():
            author.append(ch)
        elif ch.isupper():
            if author:
                break
            author.append(ch.lower())
        else:
            break
    return "".join(author) or "unknown"


def _to_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_list(v) -> list:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def _strip_at_prefix(d: dict) -> dict:
    return {k.lstrip("@"): v for k, v in d.items() if k.startswith("@")}


def _extract_alpha_node(xml: dict) -> dict:
    portfolio = xml.get("gsim", {}).get("Portfolio", {})
    alpha = portfolio.get("Alpha", {})
    if isinstance(alpha, list):
        alpha = alpha[0]
    return alpha or {}


def _extract_description(alpha_node: dict) -> dict:
    desc = alpha_node.get("Description", {}) or {}
    return _strip_at_prefix(desc)


def _extract_operations(alpha_node: dict) -> list[dict]:
    operations_block = alpha_node.get("Operations")
    if not operations_block:
        return []
    blocks = _as_list(operations_block)

    ops: list[dict] = []
    for block in blocks:
        for op in _as_list(block.get("Operation")):
            if isinstance(op, dict):
                ops.append(_strip_at_prefix(op))
    return ops


def _extract_data_modules(xml: dict) -> list[str]:
    modules = xml.get("gsim", {}).get("Modules", {}) or {}
    items = _as_list(modules.get("Data"))
    ids = []
    for it in items:
        if isinstance(it, dict) and "@id" in it:
            ids.append(it["@id"])
    return ids


def _has_intraday_curve(alpha_node: dict) -> bool:
    return alpha_node.get("IntradayCurve") is not None


def _infer_frequency(tables: list[str], has_curve: bool) -> str:
    if has_curve:
        return "intraday"
    for t in tables:
        for hint in INTRADAY_HINTS:
            if hint.lower() in t.lower():
                return "intraday"
    return "daily"


def _count_lines(py_file: Path) -> int:
    try:
        return sum(1 for _ in py_file.read_text(encoding="utf-8").splitlines())
    except Exception:
        return 0


def _check_py_syntax(py_file: Path) -> None:
    """Raises SyntaxError if invalid."""
    ast.parse(py_file.read_text(encoding="utf-8"))


def parse_factor(
    factor_dir: Path,
    config: Config,
    submitted_by: str | None = None,
    submitted_at: str | None = None,
    npy_index: dict | None = None,
) -> FactorMeta:
    xml_files = list(factor_dir.glob("*.xml"))
    py_files = list(factor_dir.glob("*.py"))
    if not xml_files:
        raise FileNotFoundError(f"no xml in {factor_dir}")
    if not py_files:
        raise FileNotFoundError(f"no py in {factor_dir}")

    xml_file = xml_files[0]
    py_file = py_files[0]

    _check_py_syntax(py_file)

    xml = load_xml(xml_file)

    alpha_node = _extract_alpha_node(xml)
    desc = _extract_description(alpha_node)

    constants = xml.get("gsim", {}).get("Constants", {}) or {}

    name = alpha_node.get("@id") or desc.get("name") or factor_dir.name
    # 目录名是命名规范 Alpha{User}{Xxx} 的权威来源,优先以目录名推断 author;
    # 推不出来(返回 unknown / 落入 _GENERIC_AUTHORS)再退回 XML Description.author
    dir_author = infer_author_from_dir(factor_dir.name)
    if dir_author not in _GENERIC_AUTHORS:
        author = dir_author
    else:
        xml_author = desc.get("author", "")
        if xml_author and xml_author.lower() not in _GENERIC_AUTHORS:
            author = xml_author
        else:
            author = "unknown"
    birthday = _to_int(desc.get("birthday"))
    universe = desc.get("universe", "")
    category = desc.get("category", "")
    delay = _to_int(alpha_node.get("@delay") or desc.get("delay"), 1)
    backdays = _to_int(constants.get("@backdays"))
    dump_alpha = str(alpha_node.get("@dumpAlphaFile", "false")).lower() == "true"
    has_curve = _has_intraday_curve(alpha_node)

    operations = _extract_operations(alpha_node)
    declared_data = _extract_data_modules(xml)

    fields = parse_datasources(py_file)
    if npy_index is None:
        npy_index = build_npy_index(config.nio_data_path)
    tables = resolve_tables(fields, npy_index)

    frequency = _infer_frequency(tables, has_curve)
    code_lines = _count_lines(py_file)

    # 原样提取,不校验(backfill 也走这里,存量因子无此字段);校验放 submit_one
    discovery_method = desc.get("discovery_method")

    return FactorMeta(
        name=name,
        author=author,
        birthday=birthday,
        universe=universe,
        category=category,
        delay=delay,
        backdays=backdays,
        dump_alpha=dump_alpha,
        has_intraday_curve=has_curve,
        operations=operations,
        declared_data_modules=declared_data,
        datasources={"fields": fields, "tables": tables},
        code_lines=code_lines,
        frequency=frequency,
        discovery_method=discovery_method,
        submitted_by=submitted_by,
        submitted_at=submitted_at,
    )

