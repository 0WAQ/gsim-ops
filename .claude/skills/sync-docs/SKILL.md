---
name: sync-docs
description: At the end of a task, update and tidy the docs so they match the code
---

# Sync Docs

Run this at the end of a task (a set of code changes) to bring the documentation
back in sync with the code: update stale `CLAUDE.md` files, `docs/`,
`.claude/plans.md`, and skills/agents, and tidy while there.

Pair with `/audit-docs`, which only *finds* drift. This skill *fixes* it.

## What to do

1. **Determine what changed.** Prefer the actual diff over guessing:
   ```bash
   git diff --stat            # uncommitted changes
   git diff --stat main...HEAD   # committed on this branch
   ```
   If the user passed a summary argument (e.g. `/sync-docs "submit absorbed
   resubmit"`), use it alongside the diff.

2. **Delegate to the `docs-updater` agent** (Agent tool, `subagent_type: docs-updater`).
   Pass the change summary + the changed paths (or the diff). The agent finds the
   affected docs, updates stale assertions to match the code, and tidies
   (merge/prune/fix links). It edits repo docs but **never memory**.

3. **Relay the result:**
   - **已更新** — list which docs the agent changed (the user reviews these in the
     git diff before committing).
   - **memory 建议改动** — surface this list to the user and note that **memory
     must be changed by hand** (it's the cross-session memory system, outside git).
     Apply those edits yourself in the main conversation only after the user is
     aware — the agent deliberately did not touch memory.
   - **存疑** — anything the agent left for human judgment.

4. **Optional re-check:** suggest running `/audit-docs changed` afterward to confirm
   no drift remains in the changed area.

## Notes

- Repo docs are auto-updated (and show up in `git diff` for review). Memory is
  never auto-updated — only suggested.
- This is manual (no hook): run it deliberately at task end, not mid-change, so it
  never rewrites docs against a half-finished state.

## Usage

```
/sync-docs                              # infer changes from git diff
/sync-docs "submit absorbed resubmit; recheck renamed restage"   # with a summary
```
