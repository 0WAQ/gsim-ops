"""Diagnostic logger (loguru).

Separate from `ops.utils.printer` — printer is user-facing CLI output;
this is the diagnostic trail that lands in ~/.cache/ops/logs/ops.log:
subprocess stderr on failure, uncaught exception tracebacks, multi-process
worker crashes.

Sinks:
- stderr: WARNING+, colorized, no traceback (terminal stays quiet on
  normal runs; printer owns the user-facing surface)
- file:   DEBUG+, with rotation/retention/compression, full backtrace.
          enqueue=True makes it ProcessPoolExecutor-safe under fork.

diagnose=False is intentional: Redis / JFS credentials travel through
call frames; diagnose=True would dump them into the log file.

Multi-process: fork-only. ProcessPoolExecutor with default context inherits
the sink queue fd; workers do NOT re-call logger.add. Switching to spawn
or forkserver breaks the queue — don't.
"""
import sys

from loguru import logger

from ops.infra.cache import CACHE_ROOT


LOG_DIR = CACHE_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logger.remove()

STDERR_SINK_ID = logger.add(
    sys.stderr,
    level="WARNING",
    format="<level>{level: <8}</level> | <cyan>{name}:{function}:{line}</cyan> - {message}",
    colorize=True,
    backtrace=False,
    diagnose=False,
    enqueue=True,
)

FILE_SINK_ID = logger.add(
    LOG_DIR / "ops.log",
    level="DEBUG",
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<dim>{process}</dim> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}:{function}:{line}</cyan> - "
        "<level>{message}</level>"
    ),
    # ANSI escape codes go directly into the file. View with `less -R`,
    # `tail` (most terminals), or `cat`. Plain `grep` matches will include
    # escape codes; use `grep --color=always` or `less` for colored search.
    colorize=True,
    rotation="20 MB",
    retention="14 days",
    compression="gz",
    enqueue=True,
    backtrace=True,
    diagnose=False,
)


__all__ = ["logger", "LOG_DIR", "STDERR_SINK_ID", "FILE_SINK_ID"]
