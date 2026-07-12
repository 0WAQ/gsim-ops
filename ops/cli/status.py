import argparse
import shutil
from typing import TYPE_CHECKING

from rich import box
from rich.console import Console
from rich.rule import Rule
from rich.table import Table

from ops.cli.common import STATUS_CHOICES, FactorStatus, add_config_arg
from ops.services.status import query_events, query_many, query_one
from ops.utils.utils import LowerAction

if TYPE_CHECKING:
    from ops.core.factor import Factor
    from ops.core.state import HistoryEvent

# ---------------------------------------------------------------------------
# 渲染(2026-07-11 展示层上收:自 services/status 迁入,services 零 rich)
# ---------------------------------------------------------------------------

_console = Console(width=shutil.get_terminal_size((140, 50)).columns)

_STATUS_STYLE = {
    FactorStatus.SUBMITTED: "green",
    FactorStatus.CHECKING:  "bold yellow",
    FactorStatus.ACTIVE:    "green",
    FactorStatus.REJECTED:  "red",
}


def _kv(key, value, width=14):
    return f"  [bold]{key:<{width}}[/] {value}"


def _outcome(c):
    if c.passed:
        return "[green]PASS[/]"
    if c.passed is False:
        return "[red]FAIL[/]"
    return "[dim]SKIP[/]"


def _print_detail(factor: "Factor", events: "list[HistoryEvent]") -> None:
    """打印单个因子的详细状态 + 生命周期时间线(factor_history,v2b)。"""
    rec = factor.state
    assert rec is not None  # 调用方已分流 info 孤儿
    _console.print(Rule(f"[bold cyan]因子状态 · {rec.name}[/]", style="cyan", characters="━"))
    style = _STATUS_STYLE.get(rec.status, "")
    _console.print(_kv("name",         rec.name))
    _console.print(_kv("author",       factor.identity.author or "—"))
    _console.print(_kv("status",       f"[{style}]{rec.status.value}[/]"))
    _console.print(_kv("submitted_at", rec.submitted_at or "—"))
    _console.print(_kv("entered_at",   rec.entered_at or "—"))
    _console.print(_kv("updated_at",   rec.updated_at))
    if factor.last_fail_stage:
        _console.print(_kv("last_fail", f"[red]{factor.last_fail_stage}[/] — {factor.last_fail_reason}"))
    if events:
        # 完整生命周期时间线(submit/check/entered/approve/restage/...)——
        # v2b 立项动机之一:详情从"检测历史"升级为全操作时间线
        _console.print(_kv("timeline", f"({len(events)})"))
        for i, e in enumerate(events, 1):
            if e.op == "check":
                line = (f"    [dim][{i}][/] {e.at}  check {_outcome(e)}")
                if e.failed_stage:
                    line += f"  [red]({e.failed_stage}: {e.fail_reason})[/]"
            else:
                line = f"    [dim][{i}][/] {e.at}  [bold]{e.op}[/]"
            if e.actor:
                line += f"  [dim]by {e.actor}[/]"
            _console.print(line)
    # (v2c:json 后端 history() 合成 check 事件,时间线渲染两后端统一,
    # 原 check_history 回落分支删除 —— record 已无该字段)
    _console.print(Rule(style="cyan", characters="━"))


def _print_list(factors: "list[Factor]") -> None:
    table = Table(box=box.SIMPLE_HEAD, header_style="bold cyan", pad_edge=False)
    table.add_column("name", no_wrap=True)
    table.add_column("status", no_wrap=True)
    table.add_column("author", no_wrap=True)
    table.add_column("updated_at", no_wrap=True)
    table.add_column("note", overflow="fold")
    for f in factors:
        rec = f.state
        if rec is None:
            table.add_row(f.name, "[red]?(无 state 记录)[/]",
                          f.identity.author or "—", "—", "info 孤儿,需对账")
            continue
        style = _STATUS_STYLE.get(rec.status, "")
        note = ""
        if rec.status == FactorStatus.REJECTED and f.last_fail_stage:
            note = f"[red]{f.last_fail_stage}[/]: {f.last_fail_reason}"
        table.add_row(
            rec.name,
            f"[{style}]{rec.status.value}[/]",
            f.identity.author or "—",
            str(rec.updated_at),
            note,
        )
    _console.print(table)


def run_status(args) -> None:
    """cli 入口:查询(services/status)→ 渲染(此处)。"""
    if args.name is not None:
        factor = query_one(args)
        if factor is None:
            _console.print(f"[yellow]未找到因子: {args.name}[/]")
            return
        if factor.state is None:
            _console.print(f"[red]⚠ {args.name} 有身份记录(factor_info)但无 state 行"
                           f" —— info 孤儿,需对账(ops rm 可清走)[/]")
            return
        _print_detail(factor, query_events(args))
        return

    factors = query_many(args)

    _console.print(Rule("[bold cyan]因子状态[/]", style="cyan", characters="━"))
    if not factors:
        _console.print("[yellow]没有匹配的因子记录[/]")
    else:
        _print_list(factors)
    _console.print(Rule(style="cyan", characters="━"))


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------

def add_status_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "status",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Example:
    ops status                       # all factors
    ops status AlphaWbaiReversal     # one factor (with check history)
    ops status -u wbai               # filter by author
    ops status -s/--status active    # filter by lifecycle status
""",
    )

    parser.add_argument("name", nargs="?", default=None, type=str, help="factor name (omit to list all)")
    parser.add_argument("--user", "-u", dest="author", default=None, type=str, action=LowerAction)
    parser.add_argument("--status", "-s", default=None, type=str, choices=list(STATUS_CHOICES))
    add_config_arg(parser)

    parser.set_defaults(func=run_status)
