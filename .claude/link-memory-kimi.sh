#!/usr/bin/env bash
# 把 kimi code 的全局指令入口(~/.kimi-code/AGENTS.md)软链到仓库内的
# .claude/memory/MEMORY.md,使 cross-session memory 跟随 git 走
# (多机滚存:git pull 即得,新机跑一次本脚本即带齐)。
# 与 link-memory.sh(claude 侧)同一模式;幂等,可反复跑。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC_MD="$REPO/.claude/memory/MEMORY.md"
DST="$HOME/.kimi-code/AGENTS.md"

if [ ! -f "$SRC_MD" ]; then
  echo "缺 $SRC_MD —— 仓库未含 .claude/memory(git pull 后重试)" >&2
  exit 1
fi
mkdir -p "$(dirname "$DST")"

if [ -L "$DST" ]; then
  cur="$(readlink "$DST")"
  if [ "$cur" = "$SRC_MD" ]; then
    echo "已是软链: $DST -> $cur"
  else
    ln -snf "$SRC_MD" "$DST"
    echo "改指: $DST -> $SRC_MD(原 $cur)"
  fi
elif [ -e "$DST" ]; then
  bak="$DST.bak.$(date +%Y%m%d-%H%M%S)"
  mv "$DST" "$bak"
  ln -s "$SRC_MD" "$DST"
  echo "$DST 已存在且非软链,备份到 $bak 后替换: -> $SRC_MD"
else
  ln -s "$SRC_MD" "$DST"
  echo "已建: $DST -> $SRC_MD"
fi
