import json
import re
import fnmatch
import shutil

from rich.console import Console
from rich.table import Table
from rich import box

from ops.core.library import LibraryScanner, FactorInfo
from ops.core.state import FactorStatus, FactorRecord
from ops.infra.store import default_store
from .metrics import load_metrics, refresh_metrics, merge_metrics
from .datasource import load_datasources, refresh_datasources, merge_datasources
from .bcorr import load_bcorr, refresh_bcorr, merge_bcorr


DASH = "—"

_console = Console(width=shutil.get_terminal_size((140, 50)).columns)

_STATUS_STYLE = {
    FactorStatus.ACTIVE:    "green",
    FactorStatus.REJECTED:  "red",
    FactorStatus.SUBMITTED: "yellow",
    FactorStatus.CHECKING:  "yellow",
    FactorStatus.DECAYING:  "magenta",
    FactorStatus.RETIRED:   "dim",
    FactorStatus.DELETED:   "dim",
}


def _fmt(v, prec=2):
    return f"{v:.{prec}f}" if v is not None else DASH


def _metric(f: FactorInfo, name: str):
    return _fmt(getattr(f.metrics, name)) if f.metrics else DASH


def _bcorr(f: FactorInfo):
    v = f.bcorr.get("max_bcorr") if f.bcorr else None
    return _fmt(v)


def _datasource(f: FactorInfo, key):
    return ", ".join(f.datasources.get(key, [])) if f.datasources else ""


def _fail_stage(rec: FactorRecord):
    if rec and rec.status == FactorStatus.REJECTED and rec.last_fail_stage:
        return rec.last_fail_stage
    return ""


# (header, justify, extras, getter(factor, record) -> str)
_BASE_COLS = [
    ("name",    "left",  {"no_wrap": True, "max_width": 36, "overflow": "ellipsis"}, lambda f, r: f.name),
    ("author",  "left",  {},                lambda f, r: f.author),
    ("delay",   "right", {},                lambda f, r: str(f.delay) if f.delay is not None else "?"),
    ("ret%",    "right", {},                lambda f, r: _metric(f, "ret")),
    ("shrp",    "right", {},                lambda f, r: _metric(f, "shrp")),
    ("mdd%",    "right", {},                lambda f, r: _metric(f, "mdd")),
    ("tvr%",    "right", {},                lambda f, r: _metric(f, "tvr")),
    ("fitness", "right", {},                lambda f, r: _metric(f, "fitness")),
    ("bcorr",   "right", {},                lambda f, r: _bcorr(f)),
]
_FAIL_COL   = ("fail_stage", "left", {},                    lambda f, r: _fail_stage(r))
_TABLES_COL = ("tables",     "left", {"overflow": "fold"},  lambda f, r: _datasource(f, "tables"))
_FIELDS_COL = ("fields",     "left", {"overflow": "fold"},  lambda f, r: _datasource(f, "fields"))


def print_table(factors: list[FactorInfo], records: dict[str, FactorRecord],
                show_tables=False, show_fields=False):
    if not factors:
        _console.print("[yellow]No factors found.[/]")
        return

    has_rejected = any(
        (rec := records.get(f.name)) and rec.status == FactorStatus.REJECTED
        for f in factors
    )

    cols = list(_BASE_COLS)
    if has_rejected: cols.append(_FAIL_COL)
    if show_tables:  cols.append(_TABLES_COL)
    if show_fields:  cols.append(_FIELDS_COL)

    table = Table(box=box.SIMPLE_HEAD, header_style="bold cyan", pad_edge=False)
    for header, justify, extras, _ in cols:
        table.add_column(header, justify=justify, **extras)

    for f in factors:
        rec = records.get(f.name)
        style = _STATUS_STYLE.get(rec.status, "") if rec else ""
        table.add_row(*(get(f, rec) for _, _, _, get in cols), style=style)

    _console.print(table)
    _console.print(f"Total: {len(factors)} factors")


def print_json(factors: list[FactorInfo]):
    data = [f.to_dict() for f in factors]
    print(json.dumps(data, indent=2, ensure_ascii=False))


SORT_KEYS = {
    "ret": lambda f: f.metrics.ret if f.metrics else float("-inf"),
    "shrp": lambda f: f.metrics.shrp if f.metrics else float("-inf"),
    "mdd": lambda f: f.metrics.mdd if f.metrics else float("-inf"),
    "tvr": lambda f: f.metrics.tvr if f.metrics else float("-inf"),
    "fitness": lambda f: f.metrics.fitness if f.metrics else float("-inf"),
    "dump_days": lambda f: f.dump_days,
    "delay": lambda f: f.delay if f.delay is not None else float("-inf"),
    "bcorr": lambda f: abs(f.bcorr["max_bcorr"]) if f.bcorr and f.bcorr.get("max_bcorr") is not None else float("-inf"),
}

METRIC_GETTERS = {
    "ret": lambda f: f.metrics.ret if f.metrics else None,
    "shrp": lambda f: f.metrics.shrp if f.metrics else None,
    "mdd": lambda f: f.metrics.mdd if f.metrics else None,
    "tvr": lambda f: f.metrics.tvr if f.metrics else None,
    "fitness": lambda f: f.metrics.fitness if f.metrics else None,
    "dump_days": lambda f: float(f.dump_days),
    "delay": lambda f: float(f.delay) if f.delay is not None else None,
    "bcorr": lambda f: abs(f.bcorr["max_bcorr"]) if f.bcorr and f.bcorr.get("max_bcorr") is not None else None,
}

_FILTER_PATTERN = re.compile(r"^(\w+)([><=!]+)(.+)$")
FILTER_KEYS = {"tables", "field"} | set(METRIC_GETTERS.keys())


def parse_filters(filter_str: str) -> list[tuple[str, str, str]] | None:
    filters = []
    has_error = False
    for part in filter_str.split(","):
        part = part.strip()
        if not part:
            continue
        m = _FILTER_PATTERN.match(part)
        if m:
            key, op, value = m.group(1), m.group(2), m.group(3)
            if key not in FILTER_KEYS:
                _console.print(f"[red]Unknown filter key:[/] '{key}'. Supported: {', '.join(sorted(FILTER_KEYS))}")
                has_error = True
                continue
            filters.append((key, op, value))
        else:
            _console.print(f"[red]Invalid filter syntax:[/] '{part}'. Expected: key=value or key>value (use quotes: --filter-by \"...\")")
            has_error = True
    if has_error:
        return None
    return filters


def apply_filters(factors: list[FactorInfo], filters: list[tuple[str, str, str]]) -> list[FactorInfo]:
    result = factors
    for key, op, value in filters:
        if key == "tables":
            result = [
                f for f in result
                if f.datasources and any(fnmatch.fnmatch(t, value) for t in f.datasources.get("tables", []))
            ]
        elif key == "field":
            result = [f for f in result if f.datasources and value in f.datasources.get("fields", [])]
        elif key in METRIC_GETTERS:
            threshold = float(value)
            getter = METRIC_GETTERS[key]
            if op == ">":
                result = [f for f in result if (v := getter(f)) is not None and v > threshold]
            elif op == ">=":
                result = [f for f in result if (v := getter(f)) is not None and v >= threshold]
            elif op == "<":
                result = [f for f in result if (v := getter(f)) is not None and v < threshold]
            elif op == "<=":
                result = [f for f in result if (v := getter(f)) is not None and v <= threshold]
            elif op == "=":
                result = [f for f in result if (v := getter(f)) is not None and v == threshold]
    return result


def run_list(args):
    scanner = LibraryScanner.from_config_path(args.config_path)
    factors = scanner.scan(refresh=args.refresh)
    records = {r.name: r for r in default_store(scanner.config).list()}
    statuses = {name: r.status for name, r in records.items()}

    if args.user:
        factors = scanner.filter_by_author(factors, args.user)

    if args.status:
        factors = [f for f in factors if statuses.get(f.name) == args.status]
    else:
        factors = [f for f in factors if statuses.get(f.name) != FactorStatus.DELETED]

    if args.refresh_metrics:
        metrics = refresh_metrics(factors, scanner.config, args.config_path)
    else:
        metrics = load_metrics(args.config_path)

    factors = merge_metrics(factors, metrics)

    if args.refresh_datasources:
        datasources = refresh_datasources(factors, scanner.config, args.config_path)
    else:
        datasources = load_datasources(args.config_path)

    factors = merge_datasources(factors, datasources)

    if args.refresh_bcorr:
        bcorr = refresh_bcorr(factors, scanner.config, args.config_path)
    else:
        bcorr = load_bcorr(args.config_path)

    factors = merge_bcorr(factors, bcorr)

    if args.filter_by is not None:
        if not args.filter_by.strip():
            _console.print("[red]Empty filter expression.[/]")
            return
        filters = parse_filters(args.filter_by)
        if filters is None:
            return
        factors = apply_filters(factors, filters)

    if args.sort_by and args.sort_by in SORT_KEYS:
        factors.sort(key=SORT_KEYS[args.sort_by], reverse=True)

    if args.n is not None:
        factors = factors[:args.n]

    if args.format == "json":
        print_json(factors)
    else:
        print_table(factors, records,
                    show_tables=args.show_tables, show_fields=args.show_fields)
