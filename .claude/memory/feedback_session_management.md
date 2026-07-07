---
name: feedback-session-management
description: User prefers one-task-per-session and active session management; suggest opening a fresh session when scope changes or context degrades
metadata: 
  node_type: memory
  type: feedback
  originSessionId: a8aa7342-1004-49e0-b73c-995062472e90
---

The user actively manages Claude Code sessions and expects me to support that. Heuristics:

- **One session, one goal.** When the user pivots to an unrelated task mid-session, suggest closing this session and opening a new one rather than dragging old context along.
- **Watch for degradation signals** and call them out: after one context compression, if I start being vague about file paths, repeating dismissed approaches, or losing earlier decisions — say so and recommend a fresh session.
- **Persist before closing.** Before a session ends, check if anything should be written to CLAUDE.md (project convention), memory (user preference), or a commit message. Don't let useful decisions die with the session.
- **Don't suggest `--continue` reflexively.** Default to fresh sessions for new tasks; `--continue` is only for genuinely resuming an interrupted task.

**Why:** The user explicitly worked through Claude Code's session model and chose this discipline. They view a long mixed-purpose session as a liability, not a feature.

**How to apply:** When the user changes topics, or when I notice context quality dropping, surface it: "this might be a good point to start a fresh session." When wrapping up a task, proactively ask whether anything should be sunk into CLAUDE.md or memory. Related: [[feedback-plan-mode]].
