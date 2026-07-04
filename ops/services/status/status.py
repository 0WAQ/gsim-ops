import shutil

from rich.console import Console
from rich.table import Table
from rich.rule import Rule
from rich import box

from ops.core.state import FactorStatus, FactorRecord
from ops.infra.config import Config
from ops.infra.store import default_store


_console = Console(width=shutil.get_terminal_size((140, 50)).columns)

_STATUS_STYLE = {
    FactorStatus.SUBMITTED: "green",
    FactorStatus.CHECKING:  "bold yellow",
    FactorStatus.ACTIVE:    "green",
    FactorStatus.REJECTED:  "red",
    FactorStatus.DECAYING:  "yellow",
    FactorStatus.RETIRED:   "dim",
}


def _kv(key, value, width=14):
    return f"  [bold]{key:<{width}}[/] {value}"


def _outcome(c):
    if c.passed:
        return "[green]PASS[/]"
    if c.passed is False:
        return "[red]FAIL[/]"
    return "[dim]SKIP[/]"


def _print_detail(rec: FactorRecord) -> None:
    _console.print(Rule(f"[bold cyan]因子状态 · {rec.name}[/]", style="cyan", characters="━"))
    style = _STATUS_STYLE.get(rec.status, "")
    _console.print(_kv("name",         rec.name))
    _console.print(_kv("author",       rec.author))
    _console.print(_kv("status",       f"[{style}]{rec.status.value}[/]"))
    _console.print(_kv("submitted_at", rec.submitted_at))
    _console.print(_kv("submitted_by", rec.submitted_by))
    _console.print(_kv("entered_at",   rec.entered_at))
    _console.print(_kv("rejected_at",  rec.rejected_at))
    _console.print(_kv("updated_at",   rec.updated_at))
    if rec.last_fail_stage:
        _console.print(_kv("last_fail", f"[red]{rec.last_fail_stage}[/] — {rec.last_fail_reason}"))
    if rec.check_history:
        _console.print(_kv("check_history", f"({len(rec.check_history)})"))
        for i, c in enumerate(rec.check_history, 1):
            line = f"    [dim][{i}][/] {c.started_at} → {c.finished_at}  {_outcome(c)}"
            if c.failed_stage:
                line += f"  [red]({c.failed_stage}: {c.fail_reason})[/]"
            _console.print(line)
    _console.print(Rule(style="cyan", characters="━"))


def _print_list(records: list[FactorRecord]) -> None:
    table = Table(box=box.SIMPLE_HEAD, header_style="bold cyan", pad_edge=False)
    table.add_column("name", no_wrap=True)
    table.add_column("status", no_wrap=True)
    table.add_column("author", no_wrap=True)
    table.add_column("updated_at", no_wrap=True)
    table.add_column("note", overflow="fold")
    for rec in records:
        style = _STATUS_STYLE.get(rec.status, "")
        note = ""
        if rec.status == FactorStatus.REJECTED and rec.last_fail_stage:
            note = f"[red]{rec.last_fail_stage}[/]: {rec.last_fail_reason}"
        table.add_row(
            rec.name,
            f"[{style}]{rec.status.value}[/]",
            rec.author,
            str(rec.updated_at),
            note,
        )
    _console.print(table)


def run_status(args) -> None:
    config = Config.load(args.config_path)
    store = default_store(config)
    name: str | None = args.name
    author: str | None = args.author
    status_filter: str | None = args.status

    if name is not None:
        rec = store.get(name)
        if rec is None:
            _console.print(f"[yellow]未找到因子: {name}[/]")
            return
        _print_detail(rec)
        return

    status_enum = FactorStatus(status_filter) if status_filter else None
    records = store.list(author=author, status=status_enum)
    records.sort(key=lambda r: r.name)

    _console.print(Rule("[bold cyan]因子状态[/]", style="cyan", characters="━"))
    if not records:
        _console.print("[yellow]没有匹配的因子记录[/]")
    else:
        _print_list(records)
    _console.print(Rule(style="cyan", characters="━"))
