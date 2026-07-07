import shutil

from rich import box
from rich.console import Console
from rich.rule import Rule
from rich.table import Table

from ops.core.state import FactorRecord, FactorStatus
from ops.infra.config import Config
from ops.infra.info import default_info_store
from ops.infra.store import default_store

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


def _print_list(records: list[tuple[FactorRecord, str | None]]) -> None:
    """打印因子列表（每项是 (FactorRecord, author) 元组）。"""
    table = Table(box=box.SIMPLE_HEAD, header_style="bold cyan", pad_edge=False)
    table.add_column("name", no_wrap=True)
    table.add_column("status", no_wrap=True)
    table.add_column("author", no_wrap=True)
    table.add_column("updated_at", no_wrap=True)
    table.add_column("note", overflow="fold")
    for rec, author in records:
        style = _STATUS_STYLE.get(rec.status, "")
        note = ""
        if rec.status == FactorStatus.REJECTED and rec.last_fail_stage:
            note = f"[red]{rec.last_fail_stage}[/]: {rec.last_fail_reason}"
        table.add_row(
            rec.name,
            f"[{style}]{rec.status.value}[/]",
            author or "—",
            str(rec.updated_at),
            note,
        )
    _console.print(table)


def run_status(args) -> None:
    config = Config.load(args.config_path)
    store = default_store(config)
    info_store = default_info_store(config)

    name: str | None = args.name
    author_filter: str | None = args.author
    status_filter: str | None = args.status

    if name is not None:
        # 单因子详情模式
        rec = store.get(name)
        if rec is None:
            _console.print(f"[yellow]未找到因子: {name}[/]")
            return
        info = info_store.get(name)
        author = info.author if info else None
        _print_detail(rec, author)
        return

    # 列表模式
    status_enum = FactorStatus(status_filter) if status_filter else None
    records = store.list(status=status_enum)

    # 按 author 过滤（需要从 factor_info 读取）
    if author_filter:
        infos = {i.name: i for i in info_store.list(author=author_filter)}
        records = [r for r in records if r.name in infos]
    else:
        infos = {i.name: i for i in info_store.list()}

    # 附加 author 信息
    records_with_author = [(r, i.author if (i := infos.get(r.name)) else None) for r in records]
    records_with_author.sort(key=lambda x: x[0].name)

    _console.print(Rule("[bold cyan]因子状态[/]", style="cyan", characters="━"))
    if not records_with_author:
        _console.print("[yellow]没有匹配的因子记录[/]")
    else:
        _print_list(records_with_author)
    _console.print(Rule(style="cyan", characters="━"))
