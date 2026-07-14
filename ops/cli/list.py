import argparse
import json
import shutil
from typing import TYPE_CHECKING

from rich import box
from rich.console import Console
from rich.table import Table

from ops.cli.common import METRIC_SORT_KEYS, STATUS_CHOICES, FactorStatus, add_config_arg
from ops.services.list import FilterError, list_factors
from ops.utils.utils import LowerAction

if TYPE_CHECKING:
    from ops.core.factor import Factor, FactorSnapshot

# ---------------------------------------------------------------------------
# 渲染(rich 渲染只在 cli,services 零 rich)
# ---------------------------------------------------------------------------

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


def _metric(snap: "FactorSnapshot | None", name: str):
    """从 snapshot 读取 metric 值。"""
    if not snap:
        return DASH
    v = getattr(snap, name, None)
    return _fmt(v)


def _bcorr(snap: "FactorSnapshot | None"):
    """从 snapshot 读取 bcorr 值。"""
    if not snap:
        return DASH
    return _fmt(snap.max_bcorr)


def _datasource(snap: "FactorSnapshot | None", key: str):
    """从 snapshot 读取 datasources (fields/tables)。"""
    if not snap:
        return ""
    vals = getattr(snap, key, None)
    return ", ".join(vals or [])


def _fail_stage(row: "Factor"):
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
_STATUS_COL = ("status",     "left", {},                    lambda x: x.status.value if x.status else "?")
_FAIL_COL   = ("fail_stage", "left", {},                    lambda x: _fail_stage(x))
_TABLES_COL = ("tables",     "left", {"overflow": "fold"},  lambda x: _datasource(x.snapshot, "tables"))
_FIELDS_COL = ("fields",     "left", {"overflow": "fold"},  lambda x: _datasource(x.snapshot, "fields"))


def print_table(rows: "list[Factor]", show_tables=False, show_fields=False):
    if not rows:
        _console.print("[yellow]No factors found.[/]")
        return

    has_rejected = any(x.status == FactorStatus.REJECTED for x in rows)

    cols = list(_BASE_COLS)
    if has_rejected:
        # 混排(含被拒)时显式列出 status —— 行颜色重定向到文件/管道即丢,
        # 被拒因子也有指标,不能只靠颜色区分
        cols.insert(2, _STATUS_COL)
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


def _row_to_json(row: "Factor") -> dict:
    """将 Factor 转换为 JSON 字典。

    ⚠ has_pnl/dump_days 两个键不在输出里 —— 它们是实时物理状态,唯一来源是
    全库扫盘(每次 list ~25s),与"list 是 PG catalog 查询"冲突。单因子的物理
    状态看 `ops info`(现场 stat,便宜);批量对账属 ops doctor。
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


def print_json(rows: "list[Factor]"):
    data = [_row_to_json(x) for x in rows]
    print(json.dumps(data, indent=2, ensure_ascii=False))


def run_list(args):
    """cli 入口:查询(services/list)→ 渲染(此处)。FilterError 逐条打印后返回。"""
    try:
        rows = list_factors(args)
    except FilterError as e:
        for msg in e.errors:
            _console.print(f"[red]{msg}[/]")
        return

    if args.format == "json":
        print_json(rows)
    else:
        print_table(rows,
                    show_tables=args.show_tables, show_fields=args.show_fields)


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------

def add_list_subparser(subparser: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparser.add_parser(
        "list",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="列出因子库中的因子",
        epilog="""\
Example:
    ops list -u wbai                        # 按作者过滤
    ops list --sort-by shrp -n 10           # 按夏普排序取前 10
    ops list --filter-by "ret>30,tables=ashare*"   # 按指标/数据源过滤
""",
    )

    parser.add_argument(
        "--user",
        "-u",
        type=str,
        action=LowerAction,
        help="Filter by author (e.g., wbai)",
    )
    parser.add_argument(
        "--status",
        "-s",
        default=None,
        type=str,
        choices=list(STATUS_CHOICES),
        help="Filter by lifecycle status (e.g., active, rejected)",
    )
    parser.add_argument(
        "--format",
        "-f",
        type=str,
        default="table",
        choices=["table", "json"],
        help="Output format (default: table)",
    )
    # --refresh 已删除:list 是纯 PG 查询,不再有扫盘索引缓存可刷新。
    parser.add_argument(
        "--show-tables",
        action="store_true",
        help="Show tables column in output",
    )
    parser.add_argument(
        "--show-fields",
        action="store_true",
        help="Show fields column in output",
    )
    parser.add_argument(
        "--filter-by",
        type=str,
        help="Filter conditions separated by commas (e.g., tables=ashareeodprices,ret>30,shrp>1.5)",
    )
    # choices 从 metric 注册表派生(SSOT,core/metrics.SNAPSHOT_METRICS):
    # delay 若要可排序,在注册表加一行(snapshot 表须有对应列)。
    parser.add_argument(
        "--sort-by",
        type=str,
        choices=list(METRIC_SORT_KEYS),
        help="Sort by field (descending)",
    )
    parser.add_argument(
        "-n",
        type=int,
        default=None,
        help="Limit output to first N factors",
    )
    add_config_arg(parser)

    parser.set_defaults(func=run_list)
