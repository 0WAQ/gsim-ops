import shutil

from rich.console import Console
from rich.tree import Tree

from ops.core.library import LibraryScanner
from ops.infra.config import Config
from ops.infra.info import default_info_store
from ops.infra.snapshot import default_snapshot_store
from ops.infra.store import default_store


_console = Console(width=shutil.get_terminal_size((140, 50)).columns)


def _kv(key, value, width=12):
    return f"[bold]{key:<{width}}[/] {value}"


_METRIC_KEYS = [("ret%:", "ret"), ("shrp:", "shrp"), ("mdd%:", "mdd"),
                ("tvr%:", "tvr"), ("fitness:", "fitness")]


def run_info(args):
    name = args.factor_name
    config = Config.load(args.config_path)

    # 存在性判据 = PG(factor_info 是三表的根)。2026-07-07 Wave 2 前用
    # "alpha_src 目录存在"判定 —— 与 status/rm/cancel 的 state 判据不一致,
    # 同一因子可能 status 里存在、info 里 not found(full-review S5)。
    info_store = default_info_store(config)
    info = info_store.get(name)
    if info is None:
        _console.print(f"[red]Factor not found:[/] {name} (factor_info 无记录)")
        _console.print("[yellow]用 ops list / ops status 确认名字;盘上目录与 PG 的漂移属对账问题[/]")
        return

    snapshot = default_snapshot_store(config).get(name)
    rec = default_store(config).get(name)

    # 物理状态:单因子现场 stat(便宜,只碰本因子路径)。scanner.get 返回 None
    # 表示 src 目录缺失(PG 有记录但盘上没有 —— 显示出来,让漂移可见)。
    scanner = LibraryScanner.from_config_path(args.config_path)
    factor = scanner.get(name)

    first_date, last_date = scanner.get_dump_date_range(name)
    date_range = f"{first_date} ~ {last_date}" if first_date else "N/A"

    status_str = rec.status.value if rec else "?(无 state 记录)"
    tree = Tree(f"[bold cyan]Factor: {name}[/]  [dim](author: {info.author or '?'}, status: {status_str})[/]")

    paths = tree.add("[yellow]Paths[/]")
    paths.add(_kv("Source:", config.alpha_src / name if factor is None else factor.src_path))
    paths.add(_kv("Dump:",   config.alpha_dump / name))
    paths.add(_kv("PNL:",    config.alpha_pnl / name))

    stats = tree.add("[yellow]Statistics[/]")
    if factor is None:
        stats.add("[red]⚠ alpha_src 目录缺失(PG 有记录但盘上没有 —— 需对账)[/]")
    else:
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
