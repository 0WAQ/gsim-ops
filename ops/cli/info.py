"""Show factor details."""
import argparse
import shutil

from rich.console import Console
from rich.tree import Tree

from ops.cli.common import add_config_arg
from ops.services.info import collect_info

# ---------------------------------------------------------------------------
# 渲染(2026-07-11 展示层上收:自 services/info 迁入,services 零 rich)
# ---------------------------------------------------------------------------

_console = Console(width=shutil.get_terminal_size((140, 50)).columns)


def _kv(key, value, width=12):
    return f"[bold]{key:<{width}}[/] {value}"


_METRIC_KEYS = [("ret%:", "ret"), ("shrp:", "shrp"), ("mdd%:", "mdd"),
                ("tvr%:", "tvr"), ("fitness:", "fitness")]


def run_info(args):
    """cli 入口:采集(services/info)→ 渲染(此处)。"""
    name = args.factor_name
    data = collect_info(args)
    if data is None:
        _console.print(f"[red]Factor not found:[/] {name} (factor_info 无记录)")
        _console.print("[yellow]用 ops list / ops status 确认名字;盘上目录与 PG 的漂移属对账问题[/]")
        return

    info = data.factor.identity
    snapshot = data.factor.snapshot
    rec = data.factor.state
    factor = data.physical

    first_date, last_date = data.date_range
    date_range = f"{first_date} ~ {last_date}" if first_date else "N/A"

    status_str = rec.status.value if rec else "?(无 state 记录)"
    tree = Tree(f"[bold cyan]Factor: {name}[/]  [dim](author: {info.author or '?'}, status: {status_str})[/]")

    fp = data.paths
    paths = tree.add("[yellow]Paths[/]")
    paths.add(_kv("Source:", fp.src if factor is None else factor.src_path))
    paths.add(_kv("Dump:",   fp.dump))
    paths.add(_kv("PNL:",    fp.pnl))

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


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------

def add_info_subparser(subparser: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparser.add_parser(
        "info",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="Show factor details",
        epilog="""\
Example:
    ops info AlphaWbaiMomentum
""",
    )

    parser.add_argument("factor_name", type=str, help="Factor name (e.g., AlphaWbaiMomentum)")
    add_config_arg(parser)

    parser.set_defaults(func=run_info)
