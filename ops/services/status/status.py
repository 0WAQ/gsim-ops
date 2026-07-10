import shutil

from rich import box
from rich.console import Console
from rich.rule import Rule
from rich.table import Table

from ops.core.factor import Factor
from ops.core.state import FactorRecord, FactorStatus
from ops.infra.config import Config
from ops.infra.repository import FactorRepository

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


def _print_detail(rec: FactorRecord, author: str | None) -> None:
    """打印单个因子的详细状态（author 从 factor_info 传入）。"""
    _console.print(Rule(f"[bold cyan]因子状态 · {rec.name}[/]", style="cyan", characters="━"))
    style = _STATUS_STYLE.get(rec.status, "")
    _console.print(_kv("name",         rec.name))
    _console.print(_kv("author",       author or "—"))
    _console.print(_kv("status",       f"[{style}]{rec.status.value}[/]"))
    _console.print(_kv("submitted_at", rec.submitted_at or "—"))
    _console.print(_kv("entered_at",   rec.entered_at or "—"))
    _console.print(_kv("rejected_at",  rec.rejected_at or "—"))
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


def _print_list(factors: list[Factor]) -> None:
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
        if rec.status == FactorStatus.REJECTED and rec.last_fail_stage:
            note = f"[red]{rec.last_fail_stage}[/]: {rec.last_fail_reason}"
        table.add_row(
            rec.name,
            f"[{style}]{rec.status.value}[/]",
            f.identity.author or "—",
            str(rec.updated_at),
            note,
        )
    _console.print(table)


def run_status(args) -> None:
    """单因子详情 / 列表。2026-07-09 阶段 3 塌缩:repo.get / repo.find
    (include_submitted=True —— status 的语义是"任何记录",单条三表 JOIN
    退役原 store.list + info_store.list 的内存合并)。"""
    config = Config.load(args.config_path)
    repo = FactorRepository(config)

    if args.name is not None:
        factor = repo.get(args.name)
        if factor is None:
            _console.print(f"[yellow]未找到因子: {args.name}[/]")
            return
        if factor.state is None:
            _console.print(f"[red]⚠ {args.name} 有身份记录(factor_info)但无 state 行"
                           f" —— info 孤儿,需对账(ops rm 可清走)[/]")
            return
        _print_detail(factor.state, factor.identity.author)
        return

    factors = repo.find(author=args.author, status=args.status,
                        include_submitted=True)

    _console.print(Rule("[bold cyan]因子状态[/]", style="cyan", characters="━"))
    if not factors:
        _console.print("[yellow]没有匹配的因子记录[/]")
    else:
        _print_list(factors)
    _console.print(Rule(style="cyan", characters="━"))
