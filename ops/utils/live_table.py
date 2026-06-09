"""Rich Live multi-row factor pipeline display.

Driver model: workers (in a ProcessPoolExecutor) emit events through a
multiprocessing.Queue; the parent's main thread drains the queue and
re-renders a Rich Live table.

Single-thread main loop (no separate drain thread). A small daemon thread
watches Future exceptions so a crashed worker that didn't get to emit
("done", ...) is still accounted for.

Reusable: pass `STAGES = (...)` per-pipeline. `ops check` uses the 6
stages; `ops run` (later) will pass `("backtest",)`.

This module only handles the display + event protocol. Workers themselves
just call `q.put(("stage_start", name, stage))` etc. — there's no logging,
no IO, no side effects beyond the queue.
"""
import queue
import threading
import time
from collections import deque
from concurrent.futures import Future, as_completed
from dataclasses import dataclass, field
from enum import Enum

from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.text import Text
from rich import box


class Status(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    PASSED    = "passed"
    FAILED    = "failed"
    SKIPPED   = "skipped"
    RETRYABLE = "retryable"

    @property
    def glyph(self) -> str:
        return _STATUS_DISPLAY[self][0]

    @property
    def style(self) -> str:
        return _STATUS_DISPLAY[self][1]


# Display info kept off the enum value so Pyright doesn't treat tuple-valued
# members as their own subtypes (a known fancy-enum inference quirk).
_STATUS_DISPLAY: dict["Status", tuple[str, str]] = {
    Status.PENDING:   ("○", "dim"),
    Status.RUNNING:   ("⏳", "yellow"),
    Status.PASSED:    ("✓", "green"),
    Status.FAILED:    ("✗", "red"),
    Status.SKIPPED:   ("⊝", "yellow"),
    Status.RETRYABLE: ("↻", "yellow"),
}


# Outcome kinds → (counter key, style)
_OUTCOME_COUNTER = {
    "pass":   "pass",
    "fail":   "fail",
    "error":  "error",
    "locked": "locked",
}


@dataclass
class FactorRow:
    idx: int                    # 1-based for display
    name: str
    stages: dict[str, Status]   # populated from STAGES
    outcome: str = ""           # "→ lib" / "→ recycle/checkbias: corr 0.87" / "locked" / ...
    outcome_style: str = ""
    started_at: float | None = None  # monotonic; reserved for future timing column

    def is_done(self) -> bool:
        return bool(self.outcome)

    def is_in_flight(self) -> bool:
        return not self.is_done() and any(s is not Status.PENDING for s in self.stages.values())


def make_factor_rows(names: list[str], stages: tuple[str, ...]) -> dict[str, FactorRow]:
    """Build the initial rows dict (all stages PENDING)."""
    return {
        name: FactorRow(idx=i + 1, name=name,
                        stages={s: Status.PENDING for s in stages})
        for i, name in enumerate(names)
    }


class LiveDriver:
    def __init__(self, rows: dict[str, FactorRow],
                 q,
                 futures: list[Future],
                 stages: tuple[str, ...],
                 console: Console,
                 recent_done_window: int = 5,
                 refresh_per_second: int = 8):
        self.rows = rows
        self.q = q
        self.futures = futures
        self.stages = stages
        self.console = console
        self.recent_done_window = recent_done_window
        self.refresh_per_second = refresh_per_second

        self._recent: deque[FactorRow] = deque(maxlen=recent_done_window)
        self._counts = {"pass": 0, "fail": 0, "error": 0, "locked": 0}

    # ---- event handling ----------------------------------------------------

    def _apply(self, ev: tuple) -> None:
        kind = ev[0]
        if kind == "stage_start":
            _, name, stage = ev
            row = self.rows.get(name)
            if row is None:
                return
            if row.started_at is None:
                row.started_at = time.monotonic()
            row.stages[stage] = Status.RUNNING
        elif kind == "stage_done":
            _, name, stage, status = ev
            row = self.rows.get(name)
            if row is None:
                return
            row.stages[stage] = status
        elif kind == "done":
            _, name, outcome_kind, note, style = ev
            row = self.rows.get(name)
            if row is None:
                return
            row.outcome = note
            row.outcome_style = style
            counter_key = _OUTCOME_COUNTER.get(outcome_kind, "error")
            self._counts[counter_key] += 1
            self._recent.append(row)

    def _watch_futures(self) -> None:
        """Synthesize a 'done' event for any future that crashed without emitting one."""
        seen_done = set()
        # NOTE: cannot easily know which factor name a crashed future maps to
        # without a side channel. For now: rely on workers to emit done in their
        # own try/except (the main except handlers in _run_one_locked already do).
        # This watcher is a backstop for the truly-fatal case (segfault, OOM kill).
        for fut in as_completed(self.futures):
            exc = fut.exception()
            if exc is None:
                continue
            # Pool worker crashed mid-task. Try to find any row that hasn't
            # emitted done yet and synthesize one. Best-effort: if multiple
            # rows are still pending, the matching is ambiguous, so we just
            # ensure the main loop unblocks.
            for row in self.rows.values():
                if row.name in seen_done or row.is_done():
                    continue
                # Heuristic: prefer rows that are in-flight over pending
                if row.is_in_flight():
                    seen_done.add(row.name)
                    self.q.put(("done", row.name, "error",
                                f"worker crashed: {type(exc).__name__}", "red"))
                    break

    # ---- rendering ---------------------------------------------------------

    def _summary_line(self) -> Text:
        total = len(self.rows)
        done = sum(1 for r in self.rows.values() if r.is_done())
        in_flight = sum(1 for r in self.rows.values() if r.is_in_flight())
        pending = total - done - in_flight
        c = self._counts
        return Text.assemble(
            ("[", "dim"), (f"{done}/{total}", "bold"), ("] ", "dim"),
            ("✓ ", "green"), (f"{c['pass']}", "green"), ("  ", ""),
            ("✗ ", "red"), (f"{c['fail']}", "red"), ("  ", ""),
            ("! ", "red"), (f"{c['error']}", "red"), ("  ", ""),
            ("locked ", "yellow"), (f"{c['locked']}", "yellow"),
            ("   in-flight ", "dim"), (f"{in_flight}", "yellow"),
            ("  pending ", "dim"), (f"{pending}", "dim"),
        )

    def _build_subtable(self, rows: list[FactorRow], title: str | None = None) -> Table:
        t = Table(box=box.SIMPLE_HEAD, header_style="bold cyan",
                  pad_edge=False, show_header=True, title=title,
                  title_justify="left", title_style="bold")
        t.add_column("#", justify="right", no_wrap=True, style="dim")
        t.add_column("name", no_wrap=True)
        for s in self.stages:
            t.add_column(s, justify="center", no_wrap=True)
        t.add_column("outcome", overflow="fold")
        for row in rows:
            cells: list[str | Text] = [str(row.idx), row.name]
            for s in self.stages:
                st = row.stages[s]
                cells.append(Text(st.glyph, style=st.style))
            cells.append(Text(row.outcome, style=row.outcome_style))
            t.add_row(*cells)
        return t

    def _render(self):
        in_flight = [r for r in self.rows.values() if r.is_in_flight()]
        in_flight.sort(key=lambda r: r.idx)
        recent = list(self._recent)

        parts: list[Text | Table] = [self._summary_line()]
        if in_flight:
            parts.append(self._build_subtable(in_flight, title="In flight"))
        if recent:
            parts.append(self._build_subtable(recent, title=f"Recent (last {len(recent)})"))
        return Group(*parts)

    # ---- degraded (non-TTY) path ------------------------------------------

    def _run_degraded(self) -> tuple[int, ...]:
        """For piped output / non-TTY: print one summary line per done event."""
        threading.Thread(target=self._watch_futures, daemon=True).start()
        remaining = len(self.futures)
        while remaining > 0:
            try:
                ev = self.q.get(timeout=0.5)
            except queue.Empty:
                continue
            self._apply(ev)
            if ev[0] == "done":
                _, name, outcome_kind, note, style = ev
                row = self.rows[name]
                self.console.print(
                    f"[dim][{row.idx}/{len(self.rows)}][/] {name}  "
                    f"[{style}]{note}[/]"
                )
                remaining -= 1
        return tuple(self._counts[k] for k in ("pass", "fail", "error", "locked"))

    # ---- main entry --------------------------------------------------------

    def run(self) -> tuple[int, ...]:
        if not self.console.is_terminal:
            return self._run_degraded()

        threading.Thread(target=self._watch_futures, daemon=True).start()
        remaining = len(self.futures)

        with Live(self._render(), console=self.console,
                  refresh_per_second=self.refresh_per_second,
                  transient=False) as live:
            while remaining > 0:
                try:
                    ev = self.q.get(timeout=0.2)
                except queue.Empty:
                    continue
                self._apply(ev)
                if ev[0] == "done":
                    remaining -= 1
                live.update(self._render())
            # final render so the last "done" lands before Live exits
            live.update(self._render())

        return tuple(self._counts[k] for k in ("pass", "fail", "error", "locked"))


__all__ = ["Status", "FactorRow", "LiveDriver", "make_factor_rows"]
