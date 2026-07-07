---
name: feedback-destructive-ops
description: "Destructive operations must be opt-in — default behavior should always be non-destructive, with --force / explicit flags required for the destructive path"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: a8aa7342-1004-49e0-b73c-995062472e90
---

Default behavior for any command must be non-destructive. Destructive variants must be opt-in via explicit flags (`--force`, `--apply`, `gc` subcommand, etc.). Never delete files, drop tables, force-push, run `rclone delete`, `rm -rf`, `git reset --hard`, etc. without:
1. The user explicitly asking, AND
2. The path being clearly named (no inferred deletion targets)

When designing new commands, mirror existing patterns in gsim-ops:
- `ops rm` — 彻底硬删已入库因子(src/pnl/dump/feature + state 行 + derived 行,不可逆,无墓碑)。默认交互确认展示完整删除清单,`-y` 跳过。**注意:2026-07-04 前 rm 是软删墓碑(DELETED 状态),现已改为彻底硬删** —— "opt-in destructive" 在这里体现为"交互确认 + 单因子接口(不加批量 -u)",不是"默认不删"。
- `ops cancel` / `ops clear` — 删未入库因子的 staging(cancel 连 state record 一起硬删)。
- `ops sync push` — additive(`rclone copy`),never `rclone delete`(sync 整体退役中)
- `--dry-run` is the right default for any new bulk operation

**Why:** The user reviews diffs and logs directly and trusts the tooling to not surprise them. A wrong-default destructive command costs real factor work. 原则不变:破坏性操作要 opt-in、清单要显示、范围不能悄悄放大。(rm 的软删→硬删是用户主动推动的语义纠正:"删除不是一种状态,删了就该不存在",见 [[project-cli-command-redesign]]。)

**How to apply:** When proposing or implementing any operation that touches files, state, or remotes — default to the non-destructive path, surface the destructive variant behind a flag, and confirm before running destructive commands even when the user seems to authorize them in passing. Match the user's specified scope exactly; don't widen it. Related: [[feedback-plan-mode]].
