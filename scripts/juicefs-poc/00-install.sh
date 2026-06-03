#!/usr/bin/env bash
# 装 redis (本机) + juicefs client。
# Client 节点(不跑 redis)设 JFS_CLIENT_ONLY=1 跳过 redis 安装。

set -euo pipefail
cd "$(dirname "$0")"
source ./_lib.sh
source ./config.sh

require_sudo

if   command -v apt-get >/dev/null; then PM=apt
elif command -v dnf >/dev/null;     then PM=dnf
elif command -v yum >/dev/null;     then PM=yum
else err "不认识的包管理器,手动装 redis"
fi

if [[ "${JFS_CLIENT_ONLY:-0}" == "1" ]]; then
  info "[skip] JFS_CLIENT_ONLY=1, 不装 redis"
else
  info "[1/2] 装 redis ($PM)"
  case $PM in
    apt) sudo apt-get update -qq && sudo apt-get install -y redis-server ;;
    dnf|yum) sudo $PM install -y redis ;;
  esac
  sudo systemctl enable --now redis-server 2>/dev/null \
    || sudo systemctl enable --now redis 2>/dev/null \
    || warn "  无法 enable 启动,看系统是否用别名"
  if redis-cli ping 2>/dev/null | grep -q PONG; then
    info "  redis PONG"
  else
    warn "  无 PONG(可能已配 requirepass,03-redis.sh 会再测)"
  fi
fi

info "[2/2] 装 juicefs client"
if command -v juicefs >/dev/null; then
  info "  已装: $(juicefs version | head -1)"
else
  curl -sSL https://d.juicefs.com/install | sudo sh -
  juicefs version | head -1
fi

echo
if [[ "${JFS_CLIENT_ONLY:-0}" == "1" ]]; then
  info "DONE (client-only)。下一步: join.sh --meta-host <主节点 IP>"
else
  info "DONE. 下一步: sudo -E bash 01-provision.sh"
fi
