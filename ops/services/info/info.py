import shutil

from rich.console import Console
from rich.tree import Tree

from ops.core.library import LibraryScanner
from ops.infra.config import Config
from ops.infra.info import default_info_store
from ops.infra.snapshot import default_snapshot_store
from ops.core.metrics import Metrics


_console = Console(width=shutil.get_terminal_size((140, 50)).columns)


def _kv(key, value, width=12):
    return f"[bold]{key:<{width}}[/] {value}"


_METRIC_KEYS = [("ret%:", "ret"), ("shrp:", "shrp"), ("mdd%:", "mdd"),
                ("tvr%:", "tvr"), ("fitness:", "fitness")]


def run_info(args):
    config = Config.load(args.config_path)
    scanner = LibraryScanner.from_config_path(args.config_path)
    factor = scanner.get(args.factor_name)

    if factor is None:
        _console.print(f"[red]Factor not found:[/] {args.factor_name}")
        _console.print(f"[yellow]Check if the factor exists in:[/] {scanner.alpha_src}")
        return

    # 从 factor_info + factor_snapshot 读取数据
    info_store = default_info_store(config)
    snapshot_store = default_snapshot_store(config)

    info = info_store.get(args.factor_name)
    snapshot = snapshot_store.get(args.factor_name)

    first_date, last_date = scanner.get_dump_date_range(factor.name)
    date_range = f"{first_date} ~ {last_date}" if first_date else "N/A"

    author = info.author if info else factor.author
    tree = Tree(f"[bold cyan]Factor: {factor.name}[/]  [dim](author: {author})[/]")

    paths = tree.add("[yellow]Paths[/]")
    paths.add(_kv("Source:", factor.src_path))
    paths.add(_kv("Dump:",   factor.dump_path))
    paths.add(_kv("PNL:",    factor.pnl_path))

    stats = tree.add("[yellow]Statistics[/]")
    stats.add(_kv("Dump Days:", factor.dump_days))
    stats.add(_kv("Date Range:", date_range))
    stats.add(_kv("Has PNL:", "[green]Yes[/]" if factor.has_pnl else "[red]No[/]"))

    m = tree.add("[yellow]Metrics (入库时快照)[/]")
    if snapshot and (snapshot.ret is not None or snapshot.shrp is not None):
        for label, attr in _METRIC_KEYS:
            val = getattr(snapshot, attr, None)
            if val is not None:
                m.add(_kv(label, f"{val:.2f}"))
            else:
                m.add(_kv(label, "—"))
        m.add(_kv("snapshot_at:", snapshot.snapshot_at or "—"))
    else:
        m.add("[dim]—  (未入库或入库时未生成 metrics)[/]")

    d = tree.add("[yellow]Data Sources (入库时)[/]")
    if snapshot and (snapshot.fields is not None or snapshot.tables is not None):
        d.add(_kv("Tables:", ", ".join(snapshot.tables or [])))
        d.add(_kv("Fields:", ", ".join(snapshot.fields or [])))
    else:
        d.add("[dim]—  (未入库或入库时未解析 datasources)[/]")

    _console.print(tree)
