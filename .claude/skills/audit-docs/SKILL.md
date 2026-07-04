---
name: audit-docs
description: Audit docs + memory for drift against the actual code (read-only)
---

# Audit Docs

Check whether the repo's documentation — `CLAUDE.md` files, `docs/`,
`.claude/plans.md`, the skills/agents themselves, and cross-session memory — still
matches what the code actually does. **Read-only**: reports drift, changes nothing.

Pair with `/sync-docs`, which *fixes* drift. This skill only *finds* it.

## What to do

1. **Resolve scope** from the argument:
   - **no arg** → whole-repo audit (all docs on the scan surface).
   - **a module name** (e.g. `check`, `submit`, `rm`) → that module's `CLAUDE.md`
     plus its cross-references (top-level command table, `docs/`, memory).
   - **`changed`** → audit only docs touching what this branch changed. First get
     the blast radius:
     ```bash
     git diff --stat main...HEAD    # committed on this branch
     git diff --stat                # uncommitted, if any
     ```
     Pass the changed paths to the agent so it focuses on the relevant docs.
   - **a file path** → audit that one doc against its code.

2. **Delegate to the `docs-auditor` agent** (Agent tool, `subagent_type: docs-auditor`).
   Pass the resolved scope (and, for `changed`, the diff/changed paths). The agent
   reads docs + code, cross-checks every assertion, and reports drift.

3. **Present the report as-is.** It's grouped by file with severity. Relay the
   **高严重汇总** prominently. Relay the **memory 建议改动** section verbatim —
   memory is only ever changed by a human in the main conversation, so surface
   those suggestions but do not act on them here.

## Notes

- This finds drift; it does not fix it. To fix, run `/sync-docs` (or apply the
  agent's suggestions manually).
- Memory (`~/.claude/projects/-home-wbai-gsim-ops/memory/`) is audited but never
  modified by tooling — its findings come back as suggestions only.

## Usage

```
/audit-docs                 # whole-repo audit
/audit-docs check           # just the check module + its cross-references
/audit-docs changed         # only docs affected by this branch's changes
/audit-docs ops/services/rm/CLAUDE.md   # one specific doc
```
