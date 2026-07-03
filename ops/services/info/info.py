import shutil

from rich.console import Console
from rich.tree import Tree

from ops.core.library import LibraryScanner
from ops.infra.config import Config
from ops.infra.derived import default_derived_store
from ops.services.list.metrics import _to_metrics


_console = Console(width=shutil.get_terminal_size((140, 50)).columns)


def _kv(key, value, width=12):
    return f"[bold]{key:<{width}}[/] {value}"


_METRIC_KEYS = [("ret%:", "ret"), ("shrp:", "shrp"), ("mdd%:", "mdd"),
                ("tvr%:", "tvr"), ("fitness:", "fitness")]


def run_info(args):
    scanner = LibraryScanner.from_config_path(args.config_path)
    factor = scanner.get(args.factor_name)

    if factor is None:
        _console.print(f"[red]Factor not found:[/] {args.factor_name}")
        _console.print(f"[yellow]Check if the factor exists in:[/] {scanner.alpha_src}")
        return

    first_date, last_date = scanner.get_dump_date_range(factor.name)
    date_range = f"{first_date} ~ {last_date}" if first_date else "N/A"
    rec = default_derived_store(Config.load(args.config_path)).get(factor.name)
    metrics = _to_metrics(rec) if rec else None

    tree = Tree(f"[bold cyan]Factor: {factor.name}[/]  [dim](author: {factor.author})[/]")

    paths = tree.add("[yellow]Paths[/]")
    paths.add(_kv("Source:", factor.src_path))
    paths.add(_kv("Dump:",   factor.dump_path))
    paths.add(_kv("PNL:",    factor.pnl_path))

    stats = tree.add("[yellow]Statistics[/]")
    stats.add(_kv("Dump Days:", factor.dump_days))
    stats.add(_kv("Date Range:", date_range))
    stats.add(_kv("Has PNL:", "[green]Yes[/]" if factor.has_pnl else "[red]No[/]"))

    m = tree.add("[yellow]Metrics[/]")
    if metrics:
        for label, attr in _METRIC_KEYS:
            m.add(_kv(label, f"{getattr(metrics, attr):.2f}"))
    else:
        m.add("[dim]—  (run ops list --refresh-metrics to fetch)[/]")

    d = tree.add("[yellow]Data Sources[/]")
    if rec and (rec.fields is not None or rec.tables is not None):
        d.add(_kv("Tables:", ", ".join(rec.tables or [])))
        d.add(_kv("Fields:", ", ".join(rec.fields or [])))
    else:
        d.add("[dim]—  (run ops list --refresh-datasources to fetch)[/]")

    _console.print(tree)
