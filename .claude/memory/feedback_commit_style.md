---
name: feedback-commit-style
description: "User wants me to write commit messages (on explicit ask). Style: English, short, no emoji, no Co-Authored-By trailer"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: a8aa7342-1004-49e0-b73c-995062472e90
---

I should write commit messages — the user prefers I draft them rather than doing it themselves. Only commit on explicit ask ("commit this", "提交一版", etc.); never auto-commit just because work is done.

Style:
- English, one short subject line (under ~70 chars), optionally a brief body
- Lowercase scope prefix when it fits the repo's pattern (this project uses things like `sync:`, `ops rm:`, `feat:`) — match recent commits with `git log`
- No emoji
- **No `Co-Authored-By: Claude` trailer** — the user does not want it

**Why:** User said "commit 这个, 我觉得还是你来帮我写比较好" — they want the drafting work delegated but reviewed. Trailer/emoji rules come from the project's existing commit history style.

**How to apply:** When the user asks to commit, run `git status` / `git diff` / `git log` first, draft a message matching recent style, omit the Claude co-author trailer. Related: [[feedback-reply-style]], [[feedback-destructive-ops]].
