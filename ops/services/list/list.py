import json
import re
import fnmatch
import shutil

from rich.console import Console
from rich.table import Table
from rich import box

from ops.core.library import LibraryScanner
from ops.core.state import FactorStatus, FactorRecord
from ops.infra.config import Config
from ops.infra.store import default_store
from ops.infra.derived import default_derived_store, DerivedRecord
from .metrics import refresh_metrics
from .datasource import refresh_datasources
from .bcorr import refresh_bcorr


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


def _metric(r: DerivedRecord, name: str):
    return _fmt(getattr(r, name))


def _bcorr(r: DerivedRecord):
    return _fmt(r.max_bcorr)


def _datasource(r: DerivedRecord, key: str):
    return ", ".join(getattr(r, key) or [])


def _fail_stage(rec: FactorRecord):
    if rec and rec.status == FactorStatus.REJECTED and rec.last_fail_stage:
        return rec.last_fail_stage
    return ""


# (header, justify, extras, getter(record, state_record) -> str)
_BASE_COLS = [
    ("name",    "left",  {"no_wrap": True, "max_width": 36, "overflow": "ellipsis"}, lambda r, s: r.name),
    ("author",  "left",  {},                lambda r, s: r.author or ""),
    ("delay",   "right", {},                lambda r, s: str(r.delay) if r.delay is not None else "?"),
    ("ret%",    "right", {},                lambda r, s: _metric(r, "ret")),
    ("shrp",    "right", {},                lambda r, s: _metric(r, "shrp")),
    ("mdd%",    "right", {},                lambda r, s: _metric(r, "mdd")),
    ("tvr%",    "right", {},                lambda r, s: _metric(r, "tvr")),
    ("fitness", "right", {},                lambda r, s: _metric(r, "fitness")),
    ("bcorr",   "right", {},                lambda r, s: _bcorr(r)),
]
_FAIL_COL   = ("fail_stage", "left", {},                    lambda r, s: _fail_stage(s))
_TABLES_COL = ("tables",     "left", {"overflow": "fold"},  lambda r, s: _datasource(r, "tables"))
_FIELDS_COL = ("fields",     "left", {"overflow": "fold"},  lambda r, s: _datasource(r, "fields"))


def print_table(records: list[DerivedRecord], state_records: dict[str, FactorRecord],
                show_tables=False, show_fields=False):
    if not records:
        _console.print("[yellow]No factors found.[/]")
        return

    has_rejected = any(
        (rec := state_records.get(r.name)) and rec.status == FactorStatus.REJECTED
        for r in records
    )

    cols = list(_BASE_COLS)
    if has_rejected: cols.append(_FAIL_COL)
    if show_tables:  cols.append(_TABLES_COL)
    if show_fields:  cols.append(_FIELDS_COL)

    table = Table(box=box.SIMPLE_HEAD, header_style="bold cyan", pad_edge=False)
    for header, justify, extras, _ in cols:
        table.add_column(header, justify=justify, **extras)

    for r in records:
        rec = state_records.get(r.name)
        style = _STATUS_STYLE.get(rec.status, "") if rec else ""
        table.add_row(*(get(r, rec) for _, _, _, get in cols), style=style)

    _console.print(table)
    _console.print(f"Total: {len(records)} factors")


def _record_json(r: DerivedRecord) -> dict:
    # Preserve the historical FactorInfo.to_dict() shape: metrics/datasources/
    # bcorr are nested dicts (or null when the group was never computed).
    metrics = None
    if r.ret is not None or r.shrp is not None or r.fitness is not None:
        metrics = {"ret%": r.ret, "tvr%": r.tvr, "shrp": r.shrp, "mdd%": r.mdd, "fitness": r.fitness}
    datasources = None
    if r.fields is not None or r.tables is not None:
        datasources = {"fields": r.fields or [], "tables": r.tables or []}
    bcorr = None
    if r.max_bcorr is not None:
        bcorr = {"max_bcorr": r.max_bcorr, "max_bcorr_factor": r.max_bcorr_factor}
    return {
        "name": r.name,
        "author": r.author,
        "has_pnl": r.has_pnl,
        "dump_days": r.dump_days,
        "delay": r.delay,
        "metrics": metrics,
        "datasources": datasources,
        "bcorr": bcorr,
    }


def print_json(records: list[DerivedRecord]):
    data = [_record_json(r) for r in records]
    print(json.dumps(data, indent=2, ensure_ascii=False))


SORT_KEYS = {
    "ret": lambda r: r.ret if r.ret is not None else float("-inf"),
    "shrp": lambda r: r.shrp if r.shrp is not None else float("-inf"),
    "mdd": lambda r: r.mdd if r.mdd is not None else float("-inf"),
    "tvr": lambda r: r.tvr if r.tvr is not None else float("-inf"),
    "fitness": lambda r: r.fitness if r.fitness is not None else float("-inf"),
    "dump_days": lambda r: r.dump_days or 0,
    "delay": lambda r: r.delay if r.delay is not None else float("-inf"),
    "bcorr": lambda r: abs(r.max_bcorr) if r.max_bcorr is not None else float("-inf"),
}

METRIC_GETTERS = {
    "ret": lambda r: r.ret,
    "shrp": lambda r: r.shrp,
    "mdd": lambda r: r.mdd,
    "tvr": lambda r: r.tvr,
    "fitness": lambda r: r.fitness,
    "dump_days": lambda r: float(r.dump_days) if r.dump_days is not None else None,
    "delay": lambda r: float(r.delay) if r.delay is not None else None,
    "bcorr": lambda r: abs(r.max_bcorr) if r.max_bcorr is not None else None,
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


def apply_filters(records: list[DerivedRecord], filters: list[tuple[str, str, str]]) -> list[DerivedRecord]:
    result = records
    for key, op, value in filters:
        if key == "tables":
            result = [
                r for r in result
                if r.tables and any(fnmatch.fnmatch(t, value) for t in r.tables)
            ]
        elif key == "field":
            result = [r for r in result if r.fields and value in r.fields]
        elif key in METRIC_GETTERS:
            threshold = float(value)
            getter = METRIC_GETTERS[key]
            if op == ">":
                result = [r for r in result if (v := getter(r)) is not None and v > threshold]
            elif op == ">=":
                result = [r for r in result if (v := getter(r)) is not None and v >= threshold]
            elif op == "<":
                result = [r for r in result if (v := getter(r)) is not None and v < threshold]
            elif op == "<=":
                result = [r for r in result if (v := getter(r)) is not None and v <= threshold]
            elif op == "=":
                result = [r for r in result if (v := getter(r)) is not None and v == threshold]
    return result


def run_list(args):
    config = Config.load(args.config_path)

    # Ensure the index (author/has_pnl/dump_days/delay) is fresh in the store.
    # scan() rebuilds from the filesystem only when alpha_src changed; otherwise
    # it's a no-op read. We ignore its return -- the store is the source now.
    LibraryScanner.from_config_path(args.config_path).scan(refresh=args.refresh)

    store = default_derived_store(config)
    state_records = {r.name: r for r in default_store(config).list()}

    # A derived row exists per factor, but `author` is only set by the index
    # scan (of alpha_src). So `author is not None` == "has an index group" ==
    # "lives in alpha_src" -- this is the list's factor set, unchanged from the
    # old scan()-driven listing. Rows with metrics/bcorr but no index (e.g. a
    # factor dropped from alpha_src leaving a stale bcorr) are correctly
    # excluded. State (now the authoritative existence source in PG) may diverge
    # from this set (staging-only submits, un-backfilled dirs); that drift is a
    # health-check concern, deliberately not surfaced here.
    records = sorted(
        (r for r in store.get_all(author=args.user).values() if r.author is not None),
        key=lambda r: r.name,
    )

    if args.status:
        records = [r for r in records
                   if (s := state_records.get(r.name)) and s.status == args.status]
    else:
        records = [r for r in records
                   if not (s := state_records.get(r.name)) or s.status != FactorStatus.DELETED]

    # Refresh derived groups for the (already filtered) set, then re-read those
    # rows so the refreshed values show. Matches old behavior: refresh operated
    # on the filtered factors, not the whole library.
    if args.refresh_metrics or args.refresh_datasources or args.refresh_bcorr:
        names = [r.name for r in records]
        if args.refresh_metrics:
            refresh_metrics(names, config, args.config_path)
        if args.refresh_datasources:
            refresh_datasources(names, config, args.config_path)
        if args.refresh_bcorr:
            refresh_bcorr(names, config, args.config_path)
        refreshed = store.get_all()
        records = [refreshed[r.name] for r in records if r.name in refreshed]

    if args.filter_by is not None:
        if not args.filter_by.strip():
            _console.print("[red]Empty filter expression.[/]")
            return
        filters = parse_filters(args.filter_by)
        if filters is None:
            return
        records = apply_filters(records, filters)

    if args.sort_by and args.sort_by in SORT_KEYS:
        records.sort(key=SORT_KEYS[args.sort_by], reverse=True)

    if args.n is not None:
        records = records[:args.n]

    if args.format == "json":
        print_json(records)
    else:
        print_table(records, state_records,
                    show_tables=args.show_tables, show_fields=args.show_fields)
