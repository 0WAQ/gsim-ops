#!/usr/bin/env bash
# 把 Claude Code 的 memory 目录软链到仓库内的 .claude/memory/,
# 使 cross-session memory 跟随 git 走(个人项目、多机同步用)。
# 新机 clone 仓库后跑一次即可;幂等。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DST="$REPO/.claude/memory"

# Claude Code 用工作目录的绝对路径编码成 ~/.claude/projects/<encoded>/
# 编码规则:前导 / 去掉后,其余 / 替换为 -,再补前导 -
ENCODED="-$(echo "${REPO#/}" | tr '/' '-')"
SRC="$HOME/.claude/projects/$ENCODED/memory"

mkdir -p "$DST"
mkdir -p "$(dirname "$SRC")"

if [ -L "$SRC" ]; then
  echo "已是软链: $SRC -> $(readlink "$SRC")"
elif [ -e "$SRC" ]; then
  echo "警告: $SRC 是真实目录,先合并再软链" >&2
  cp -n "$SRC"/*.md "$DST"/ 2>/dev/null || true
  rm -rf "$SRC"
  ln -s "$DST" "$SRC"
  echo "已合并并软链: $SRC -> $DST"
else
  ln -s "$DST" "$SRC"
  echo "已软链: $SRC -> $DST"
fi
