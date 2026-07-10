import fnmatch
import json
import re
import shutil

from rich import box
from rich.console import Console
from rich.table import Table

from ops.core.factor import Factor, FactorSnapshot
from ops.core.state import FactorStatus
from ops.infra.config import Config
from ops.infra.repository import FactorRepository

DASH = "—"

_console = Console(width=shutil.get_terminal_size((140, 50)).columns)

_STATUS_STYLE = {
    FactorStatus.ACTIVE:    "green",
    FactorStatus.REJECTED:  "red",
    FactorStatus.SUBMITTED: "yellow",
    FactorStatus.CHECKING:  "yellow",
}


def _fmt(v, prec=2):
    return f"{v:.{prec}f}" if v is not None else DASH


def _metric(snap: FactorSnapshot | None, name: str):
    """从 snapshot 读取 metric 值。"""
    if not snap:
        return DASH
    v = getattr(snap, name, None)
    return _fmt(v)


def _bcorr(snap: FactorSnapshot | None):
    """从 snapshot 读取 bcorr 值。"""
    if not snap:
        return DASH
    return _fmt(snap.max_bcorr)


def _datasource(snap: FactorSnapshot | None, key: str):
    """从 snapshot 读取 datasources (fields/tables)。"""
    if not snap:
        return ""
    vals = getattr(snap, key, None)
    return ", ".join(vals or [])


def _fail_stage(row: Factor):
    if row.status == FactorStatus.REJECTED and row.last_fail_stage:
        return row.last_fail_stage
    return ""


# (header, justify, extras, getter(row: Factor) -> str)
_BASE_COLS = [
    ("name",    "left",  {"no_wrap": True, "max_width": 36, "overflow": "ellipsis"}, lambda x: x.identity.name),
    ("author",  "left",  {},                lambda x: x.identity.author or ""),
    ("delay",   "right", {},                lambda x: str(x.snapshot.delay) if x.snapshot and x.snapshot.delay is not None else "?"),
    ("ret%",    "right", {},                lambda x: _metric(x.snapshot, "ret")),
    ("shrp",    "right", {},                lambda x: _metric(x.snapshot, "shrp")),
    ("mdd%",    "right", {},                lambda x: _metric(x.snapshot, "mdd")),
    ("tvr%",    "right", {},                lambda x: _metric(x.snapshot, "tvr")),
    ("fitness", "right", {},                lambda x: _metric(x.snapshot, "fitness")),
    ("bcorr",   "right", {},                lambda x: _bcorr(x.snapshot)),
]
_FAIL_COL   = ("fail_stage", "left", {},                    lambda x: _fail_stage(x))
_TABLES_COL = ("tables",     "left", {"overflow": "fold"},  lambda x: _datasource(x.snapshot, "tables"))
_FIELDS_COL = ("fields",     "left", {"overflow": "fold"},  lambda x: _datasource(x.snapshot, "fields"))


def print_table(rows: list[Factor], show_tables=False, show_fields=False):
    if not rows:
        _console.print("[yellow]No factors found.[/]")
        return

    has_rejected = any(x.status == FactorStatus.REJECTED for x in rows)

    cols = list(_BASE_COLS)
    if has_rejected:
        cols.append(_FAIL_COL)
    if show_tables:
        cols.append(_TABLES_COL)
    if show_fields:
        cols.append(_FIELDS_COL)

    table = Table(box=box.SIMPLE_HEAD, header_style="bold cyan", pad_edge=False)
    for header, justify, extras, _ in cols:
        table.add_column(header, justify=justify, **extras)

    for x in rows:
        style = _STATUS_STYLE.get(x.status, "") if x.status else ""
        table.add_row(*(get(x) for _, _, _, get in cols), style=style)

    _console.print(table)
    _console.print(f"Total: {len(rows)} factors")


def _row_to_json(row: Factor) -> dict:
    """将 Factor 转换为 JSON 字典。

    ⚠ 2026-07-07 Wave 2 输出变更: has_pnl/dump_days 两个键移除 —— 它们是实时
    物理状态,唯一来源是全库扫盘(每次 list ~25s),与"list 是 PG catalog 查询"
    冲突。单因子的物理状态看 `ops info`(现场 stat,便宜);批量对账属后续
    ops doctor。新增 status 键(因子集判据变更后调用方常需要)。
    """
    snap = row.snapshot

    metrics = None
    if snap and (snap.ret is not None or snap.shrp is not None or snap.fitness is not None):
        metrics = {"ret%": snap.ret, "tvr%": snap.tvr, "shrp": snap.shrp, "mdd%": snap.mdd, "fitness": snap.fitness}

    datasources = None
    if snap and (snap.fields is not None or snap.tables is not None):
        datasources = {"fields": snap.fields or [], "tables": snap.tables or []}

    bcorr = None
    if snap and snap.max_bcorr is not None:
        bcorr = {"max_bcorr": snap.max_bcorr, "max_bcorr_factor": snap.max_bcorr_factor}

    return {
        "name": row.identity.name,
        "author": row.identity.author,
        "status": row.status.value if row.status else None,
        "delay": snap.delay if snap else None,
        "metrics": metrics,
        "datasources": datasources,
        "bcorr": bcorr,
    }


def print_json(rows: list[Factor]):
    data = [_row_to_json(x) for x in rows]
    print(json.dumps(data, indent=2, ensure_ascii=False))


_FILTER_PATTERN = re.compile(r"^(\w+)([><=!]+)(.+)$")
# 可排序/过滤的 metric 键（从 snapshot 读取）。dump_days 已移除 —— 它是实时物理状态
# (不在 snapshot)，过滤/排序无对应快照列，故不再作为 filter/sort 键。
_SORTABLE_KEYS = {"ret", "shrp", "mdd", "tvr", "fitness", "bcorr"}
FILTER_KEYS = {"tables", "field"} | _SORTABLE_KEYS
# 合法比较符白名单:typo(=>、=<、>< 等)能通过正则但下推白名单和内存 if 链都
# 没有分支 —— 旧行为是**静默吞掉该条件**且新旧路径因子集还不一致(对抗评审),
# 一律在解析期响亮拒绝。
_VALID_OPS = {">", ">=", "<", "<=", "=", "!="}


def _metric_get(snap: FactorSnapshot | None, key: str) -> float | None:
    """从 snapshot 获取 metric 值（用于过滤/排序）。"""
    if not snap:
        return None
    if key == "bcorr":
        return abs(snap.max_bcorr) if snap.max_bcorr is not None else None
    return getattr(snap, key, None)


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
            if op not in _VALID_OPS:
                hint = " (did you mean '>=')" if op == "=>" else \
                       " (did you mean '<=')" if op == "=<" else ""
                _console.print(f"[red]Unknown operator:[/] '{op}'{hint}. "
                               f"Supported: {', '.join(sorted(_VALID_OPS))}")
                has_error = True
                continue
            filters.append((key, op, value))
        else:
            _console.print(f"[red]Invalid filter syntax:[/] '{part}'. Expected: key=value or key>value (use quotes: --filter-by \"...\")")
            has_error = True
    if has_error:
        return None
    return filters


def apply_filters(rows: list[Factor], filters: list[tuple[str, str, str]]) -> list[Factor]:
    """内存侧过滤（兜底，保证与下推结果逐位等价）。"""
    result = rows
    for key, op, value in filters:
        if key == "tables":
            result = [
                x for x in result
                if x.snapshot and x.snapshot.tables and any(fnmatch.fnmatch(t, value) for t in x.snapshot.tables)
            ]
        elif key == "field":
            result = [x for x in result if x.snapshot and x.snapshot.fields and value in x.snapshot.fields]
        elif key in _SORTABLE_KEYS:
            threshold = float(value)
            if op == ">":
                result = [x for x in result if (v := _metric_get(x.snapshot, key)) is not None and v > threshold]
            elif op == ">=":
                result = [x for x in result if (v := _metric_get(x.snapshot, key)) is not None and v >= threshold]
            elif op == "<":
                result = [x for x in result if (v := _metric_get(x.snapshot, key)) is not None and v < threshold]
            elif op == "<=":
                result = [x for x in result if (v := _metric_get(x.snapshot, key)) is not None and v <= threshold]
            elif op == "=":
                result = [x for x in result if (v := _metric_get(x.snapshot, key)) is not None and v == threshold]
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
    """列出库内因子 —— 零扫盘,纯 PG catalog 查询。

    2026-07-07 Wave 2 (JOURNAL V1): 因子集判据收敛为
    `factor_state.status != 'submitted'`(在 repo.find 里定义,PG 是唯一
    权威)。原扫盘白名单 + derived 索引缓存路径删除 —— 缓存自三表迁移起已坏,
    每次 list 都在付 ~25s 全库扫盘税(full-review P0-4/G6)。PG 与磁盘的漂移
    属对账问题(后续 ops doctor),不由 list 承担。
    """
    config = Config.load(args.config_path)

    # Parse --filter-by up front so datasource conditions (field= / tables=) can
    # be pushed down into snapshot_store.list (SQL/GIN on the PG backend).
    # apply_filters still runs the full filter set below, so pushdown is a pure
    # pre-filter.
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

    # repo.find 单条三表 LEFT JOIN(author/field/tables/metrics/status/sort
    # 下推;2026-07-09 退役 query_factors 的三次查 + 内存合并)。下面仍全量跑
    # 一遍 filter/status/sort/[:n],故下推纯为预筛,结果与不下推逐位等价。
    # limit 不下推,由此处 [:n] 在内存过滤后截断(P0-5 语义:先滤后截)。
    rows = FactorRepository(config).find(
        author=args.user, field=field_pd, table_glob=table_pd,
        metrics=metric_pd,
        status=args.status, sort_by=sort_pd,
    )
    # 兜底基线:默认 name ASC,下方 sort/filter 再叠加。
    rows.sort(key=lambda x: x.identity.name)

    if args.status:
        rows = [x for x in rows if x.status is not None and x.status.value == args.status]

    if filters is not None:
        rows = apply_filters(rows, filters)

    if args.sort_by and args.sort_by in _SORTABLE_KEYS:
        rows.sort(key=lambda x: _metric_get(x.snapshot, args.sort_by) or 0, reverse=True)

    if args.n is not None:
        rows = rows[:args.n]

    if args.format == "json":
        print_json(rows)
    else:
        print_table(rows,
                    show_tables=args.show_tables, show_fields=args.show_fields)
