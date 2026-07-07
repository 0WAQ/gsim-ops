---
name: feedback-reply-style
description: "User prefers terse Chinese replies, terminal-friendly output, no emoji anywhere"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: a8aa7342-1004-49e0-b73c-995062472e90
---

Reply in Chinese by default. **Never output Japanese** — not a single word, not in prose, comments, or examples (user explicitly called this out 2026-07-01; the model sometimes drifts into Japanese particles/kana). Keep responses tight — direct answers over preambles, no trailing recaps unless asked. Never use emoji in chat replies, code, commit messages, or file content. Terminal output (CLI tools in this project) should also stay emoji-free unless explicitly requested.

**Why:** The user works primarily in a terminal and reviews diffs/logs directly; emoji clutter and English boilerplate ("I'll now...", "Here's a summary...") slow them down.

**How to apply:** Match the language of the user's message (Chinese in → Chinese out). Skip "let me..." preambles before tool calls beyond a single sentence. No emoji in any artifact I write. Related: [[feedback-commit-style]].
