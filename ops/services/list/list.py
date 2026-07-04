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
from ops.infra.derived.base import metric_get, sort_key, _SORTABLE_KEYS


DASH = "—"

_console = Console(width=shutil.get_terminal_size((140, 50)).columns)

_STATUS_STYLE = {
    FactorStatus.ACTIVE:    "green",
    FactorStatus.REJECTED:  "red",
    FactorStatus.SUBMITTED: "yellow",
    FactorStatus.CHECKING:  "yellow",
    FactorStatus.DECAYING:  "magenta",
    FactorStatus.RETIRED:   "dim",
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


_FILTER_PATTERN = re.compile(r"^(\w+)([><=!]+)(.+)$")
FILTER_KEYS = {"tables", "field"} | set(_SORTABLE_KEYS)


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
        elif key in _SORTABLE_KEYS:
            threshold = float(value)
            if op == ">":
                result = [r for r in result if (v := metric_get(r, key)) is not None and v > threshold]
            elif op == ">=":
                result = [r for r in result if (v := metric_get(r, key)) is not None and v >= threshold]
            elif op == "<":
                result = [r for r in result if (v := metric_get(r, key)) is not None and v < threshold]
            elif op == "<=":
                result = [r for r in result if (v := metric_get(r, key)) is not None and v <= threshold]
            elif op == "=":
                result = [r for r in result if (v := metric_get(r, key)) is not None and v == threshold]
    return result


def _pushdown_params(filters: list[tuple[str, str, str]]) -> tuple[str | None, str | None]:
    """从已解析的 filters 里挑出第一个 field= 和第一个 tables= 条件的 value,
    作为 get_all 的 SQL 下推参数。多个同类条件时只下推第一个,其余留给
    apply_filters 内存兜底 —— 下推只做预筛,不改变最终结果。"""
    field = next((v for k, _, v in filters if k == "field"), None)
    table_glob = next((v for k, _, v in filters if k == "tables"), None)
    return field, table_glob


def _metric_pushdown(filters: list[tuple[str, str, str]]) -> list[tuple[str, str, float]]:
    """把 metric 阈值条件 (ret>30 等) 转成 get_all 的下推参数。
    `!=` 不下推 (apply_filters 未实现,现状静默忽略),剔除以保持逐位等价;
    apply_filters 仍全量兜底,故下推纯为预筛。"""
    out: list[tuple[str, str, float]] = []
    for key, op, value in filters:
        if key in _SORTABLE_KEYS and op != "!=":
            out.append((key, op, float(value)))
    return out


def run_list(args):
    config = Config.load(args.config_path)

    # Ensure the index (author/has_pnl/dump_days/delay) is fresh in the store.
    # scan() rebuilds from the filesystem only when alpha_src changed; otherwise
    # it's a no-op read. We ignore its return -- the store is the source now.
    LibraryScanner.from_config_path(args.config_path).scan(refresh=args.refresh)

    # Parse --filter-by up front so datasource conditions (field= / tables=) can
    # be pushed down into get_all (SQL/GIN on the PG backend). apply_filters still
    # runs the full filter set below, so pushdown is a pure pre-filter.
    filters: list[tuple[str, str, str]] | None = None
    if args.filter_by is not None:
        if not args.filter_by.strip():
            _console.print("[red]Empty filter expression.[/]")
            return
        filters = parse_filters(args.filter_by)
        if filters is None:
            return

    field_pd, table_pd = _pushdown_params(filters) if filters else (None, None)
    metric_pd = _metric_pushdown(filters) if filters else []
    sort_pd = args.sort_by if args.sort_by in _SORTABLE_KEYS else None

    # limit 下推 gate:limit 减少行数,不是纯预筛。只有当 SQL 结果集 == 最终结果集
    # 时才能下推,否则 SQL 之后的 Python 过滤会把行数砍到 < n。因此仅当:
    #   - 无 --status (state 表过滤,本轮不 JOIN 下推);
    #   - 无 field=/tables= 过滤 (field 精确、tables LIKE 近似,后者仍需内存兜底,
    #     且两者都可能被同类第二条件二次过滤)。
    # 命中时 SQL 已按 sort_pd 排序 + author IS NOT NULL,limit 后即最终结果。
    # metric 阈值下推是精确的 (与 apply_filters 逐位等价),不影响 gate。
    can_push_limit = args.status is None and field_pd is None and table_pd is None
    limit_pd = args.n if can_push_limit else None

    store = default_derived_store(config)
    state_records = {r.name: r for r in default_store(config).list()}

    # A derived row exists per factor, but `author` is only set by the index
    # scan (of alpha_src). So `author is not None` == "has an index group" ==
    # "lives in alpha_src" -- this is the list's factor set, unchanged from the
    # old scan()-driven listing. Rows with metrics/bcorr but no index (e.g. a
    # factor dropped from alpha_src leaving a stale bcorr) are correctly
    # excluded (has_index=True pushes this into SQL). State (now the
    # authoritative existence source in PG) may diverge from this set
    # (staging-only submits, un-backfilled dirs); that drift is a health-check
    # concern, deliberately not surfaced here.
    #
    # get_all pushes author/has_index/field/tables/metrics/sort/limit into SQL
    # (or the json backend's in-memory mirror). Everything below still runs the
    # full filter/sort/limit set as a fallback, so a partial/absent pushdown is
    # a pure pre-filter -- results are bit-for-bit identical either way.
    records = list(
        store.get_all(
            author=args.user, has_index=True,
            field=field_pd, table_glob=table_pd,
            metrics=metric_pd, sort_by=sort_pd, limit=limit_pd,
        ).values()
    )
    # 兜底基线:默认 name ASC (get_all 不带 sort_by 时的顺序),下方 sort/filter 再叠加。
    records.sort(key=lambda r: r.name)

    if args.status:
        records = [r for r in records
                   if (s := state_records.get(r.name)) and s.status == args.status]

    if filters is not None:
        records = apply_filters(records, filters)

    if args.sort_by and args.sort_by in _SORTABLE_KEYS:
        records.sort(key=lambda r: sort_key(r, args.sort_by), reverse=True)

    if args.n is not None:
        records = records[:args.n]

    if args.format == "json":
        print_json(records)
    else:
        print_table(records, state_records,
                    show_tables=args.show_tables, show_fields=args.show_fields)
