---
name: feedback-plan-mode
description: "For non-trivial changes (3+ files / new modules / architectural shifts), use EnterPlanMode first and write durable plans into .claude/plans.md (NOT CLAUDE.md)"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: a8aa7342-1004-49e0-b73c-995062472e90
---

For non-trivial work, use EnterPlanMode before writing any code. After the plan is approved and (partially) executed, if the work spans multiple sessions or has follow-ups, write the plan into `.claude/plans.md` so the next session has the context. Add a one-line entry in `.claude/roadmap.md` if it's a tracked feature.

**Where plans live** (corrected 2026-06-01):
- `.claude/plans.md` — detailed deferred plans (long-form design, migration phases, rationale)
- `.claude/roadmap.md` — feature checklist (one line per item, links to plans.md for detail)
- `CLAUDE.md` `## Plans & Roadmap` — short-form recent state only (recently fixed bugs, in-progress phases). Long-term plans go to `.claude/plans.md` with a one-line pointer from CLAUDE.md if needed
- Do NOT write long-form plans directly into CLAUDE.md. The user corrected this once

Triggers for plan mode:
- Changes touching 3+ files
- New top-level modules or CLI subcommands
- Architectural decisions (data model, sync semantics, lifecycle states, storage backend)
- Anything where multiple valid approaches exist and the choice has long-term consequences

Skip plan mode for:
- Single-function fixes
- Renames / typo fixes
- Adding a flag to an existing command

**Why:** The user has built a habit of staging design discussions in `.claude/plans.md`. They prefer reviewing a written plan over a freeform conversation, and they want plans to survive across sessions. CLAUDE.md is for codebase guidance, not durable plans.

**How to apply:** When the user proposes a meaningful change, default to EnterPlanMode and write the plan file. After approval, if there's anything that affects future sessions (new convention, deferred subtask, design rationale), add a section to `.claude/plans.md` and a checkbox to `.claude/roadmap.md`. Mark completed plans by moving them to a Done section or deleting them — don't let plans.md grow unbounded. Related: [[feedback-session-management]].
