"""Reconcile state.json against the filesystem.

Designed to run at the start of `ops check` (and exposed via `ops check
--reconcile-only` or future `ops doctor`). Catches orphans that accumulate
when a process is killed between a filesystem move and the matching state
transition.

Reconciliation rules:

  state status | filesystem location | action
  ─────────────┼─────────────────────┼─────────────────────────────────
  SUBMITTED    | staging/            | OK
  SUBMITTED    | alpha_src/          | → ACTIVE (move finished, state didn't)
  SUBMITTED    | recycle/            | → REJECTED (last_fail_stage from path)
  SUBMITTED    | (nowhere)           | drop record
  CHECKING     | staging/            | → SUBMITTED (crashed mid-check)
  CHECKING     | alpha_src/          | → ACTIVE
  CHECKING     | recycle/            | → REJECTED
  CHECKING     | (nowhere)           | drop record
  ACTIVE       | alpha_src/          | OK
  ACTIVE       | elsewhere/nowhere   | warn (don't auto-fix — surprising)
  REJECTED     | recycle/            | OK
  REJECTED     | elsewhere/nowhere   | warn

We touch state, not the filesystem. Filesystem is the source of truth.
"""
from datetime import datetime
from pathlib import Path

from ops.core.state import FactorStatus
from ops.infra.config import Config
from ops.infra.store import StateStore
from ops.utils.logger.log import info, warn


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _scan(root: Path) -> dict[str, Path]:
    if not root.exists():
        return {}
    return {d.name: d for d in root.iterdir() if d.is_dir() and d.name.startswith("Alpha")}


def _scan_recycle(root: Path) -> dict[str, tuple[Path, str]]:
    """{name: (dir, stage)}; recycle layout is recycle/{user}/{stage}/AlphaXxx/."""
    out: dict[str, tuple[Path, str]] = {}
    if not root.exists():
        return out
    for user_dir in root.iterdir():
        if not user_dir.is_dir():
            continue
        for stage_dir in user_dir.iterdir():
            if not stage_dir.is_dir():
                continue
            for d in stage_dir.iterdir():
                if d.is_dir() and d.name.startswith("Alpha"):
                    out[d.name] = (d, stage_dir.name)
    return out


def reconcile(config: Config, store: StateStore) -> dict[str, int]:
    """Walk state vs filesystem, repair drift. Returns a counter."""
    staging = _scan(config.staging)
    alpha_src = _scan(config.alpha_src)
    recycle = _scan_recycle(config.recycle)

    counts = {"ok": 0, "promoted_active": 0, "promoted_rejected": 0,
              "reverted_submitted": 0, "dropped": 0, "warned": 0}

    for rec in store.list():
        name = rec.name
        st = rec.status

        if st == FactorStatus.SUBMITTED:
            if name in staging:
                counts["ok"] += 1
            elif name in alpha_src:
                store.transition(name, FactorStatus.ACTIVE, entered_at=_now())
                info(f"  ⚙  {name} SUBMITTED → ACTIVE (found in alpha_src)")
                counts["promoted_active"] += 1
            elif name in recycle:
                _, stage = recycle[name]
                store.transition(name, FactorStatus.REJECTED, rejected_at=_now(),
                                 last_fail_stage=stage,
                                 last_fail_reason="reconciled from filesystem")
                info(f"  ⚙  {name} SUBMITTED → REJECTED (found in recycle/{stage})")
                counts["promoted_rejected"] += 1
            else:
                store.delete(name)
                info(f"  ⚙  {name} SUBMITTED record dropped (no files on disk)")
                counts["dropped"] += 1

        elif st == FactorStatus.CHECKING:
            if name in staging:
                store.transition(name, FactorStatus.SUBMITTED)
                info(f"  ⚙  {name} CHECKING → SUBMITTED (crashed mid-check)")
                counts["reverted_submitted"] += 1
            elif name in alpha_src:
                store.transition(name, FactorStatus.ACTIVE, entered_at=_now())
                info(f"  ⚙  {name} CHECKING → ACTIVE")
                counts["promoted_active"] += 1
            elif name in recycle:
                _, stage = recycle[name]
                store.transition(name, FactorStatus.REJECTED, rejected_at=_now(),
                                 last_fail_stage=stage,
                                 last_fail_reason="reconciled from filesystem")
                info(f"  ⚙  {name} CHECKING → REJECTED (recycle/{stage})")
                counts["promoted_rejected"] += 1
            else:
                store.delete(name)
                info(f"  ⚙  {name} CHECKING record dropped (no files on disk)")
                counts["dropped"] += 1

        elif st == FactorStatus.ACTIVE:
            if name in alpha_src:
                counts["ok"] += 1
            else:
                warn(f"  ⚠  {name} ACTIVE but not in alpha_src — manual review")
                counts["warned"] += 1

        elif st == FactorStatus.REJECTED:
            if name in recycle:
                counts["ok"] += 1
            else:
                warn(f"  ⚠  {name} REJECTED but not in recycle — manual review")
                counts["warned"] += 1

    return counts
