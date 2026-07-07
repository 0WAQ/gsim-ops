---
name: run-it-yourself
description: "能自己执行的命令直接跑,不要 dump 给用户重复执行"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: cf00be5b-1356-44f4-ba43-eb1cbbef0949
---

能用 Bash tool 跑的命令,我自己跑;不要把命令贴给用户让他复制粘贴执行。

**Why:** 用户明确反感这种"让我帮你跑"的对话节奏。每条让用户跑的命令 = 一次复制粘贴 + 等输出 + 贴回来 + 我再分析。慢且打断思路。

**How to apply:**
- 本地命令(读文件 / 跑 Python / 本地 git / bash 诊断)— **直接 Bash tool 跑**,不要让用户跑
- 多步诊断写成一个临时 bash 脚本(/tmp/foo.sh)然后自己 invoke,不要让用户逐条复制
- **必须给用户跑的场景**(且仅这些):
  - 需要 sudo 密码 而我没 TTY(用户机器上的 root-only 文件 / systemctl 操作)
  - 远端 SSH 我没凭证(默认假设没有,先用 `ssh -o BatchMode=yes ... echo ok` 测一下,通就自己 SSH)
  - `git push` / `git fetch` 远端(VSCode credential helper socket 不通)
  - 需要交互输入(密码 prompt / `read -p` / `vim`)
- 给用户跑前先问自己:这是 sudo / 远端没凭证 / push / 交互 中的哪个?不在这个清单 → 自己跑
- 即使我以为"用户跑会快",也不跑;让用户跑的体感成本远高于我自己跑慢一点

[[run-it-yourself]] 关联到 [[feedback_destructive_ops]](破坏性操作仍然要先确认),但只读 / 诊断 / 本地 read-only 应该默认自己跑
