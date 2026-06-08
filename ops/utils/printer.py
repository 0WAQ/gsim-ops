import shutil

from rich.console import Console
from rich.rule import Rule


_console = Console(width=shutil.get_terminal_size((140, 50)).columns)


def base(style, msg, *args, **kw):
    _console.print(msg, *args, style=style, markup=False, highlight=False, **kw)


def info(msg, *args, **kw):
    base("green", msg, *args, **kw)


def warn(msg, *args, **kw):
    base("yellow", msg, *args, **kw)


def error(msg, *args, **kw):
    base("red", msg, *args, **kw)


def highlight(msg, *args, **kw):
    base("bold yellow", msg, *args, **kw)


def progress(prefix, name):
    """Print '<prefix>' (plain) + '<name>' (bold yellow) on the same line."""
    _console.print(prefix, end="", markup=False, highlight=False)
    _console.print(name, style="bold yellow", markup=False, highlight=False)


def banner(title):
    _console.print(Rule(f"[bold cyan]{title}[/]", style="cyan", characters="━"))


def bottom():
    _console.print(Rule(style="cyan", characters="━"))
