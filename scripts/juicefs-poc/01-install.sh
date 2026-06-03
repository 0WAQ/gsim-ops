#!/usr/bin/env bash
# Install Redis (本机) + JuiceFS client. Requires sudo.
# 跨节点:在 client-only 节点(不跑 Redis)上设 JFS_CLIENT_ONLY=1 跳过 redis 安装。

set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh

JFS_CLIENT_ONLY="${JFS_CLIENT_ONLY:-0}"

if command -v apt-get >/dev/null; then PM=apt
elif command -v dnf >/dev/null; then PM=dnf
elif command -v yum >/dev/null; then PM=yum
else echo "unsupported pkg mgr; install redis manually" >&2; exit 1
fi

if [[ "$JFS_CLIENT_ONLY" == "1" ]]; then
  echo "[skip] JFS_CLIENT_ONLY=1, 不装 redis"
else
  echo "[1/4] installing redis ($PM)..."
  case $PM in
    apt) sudo apt-get update -qq && sudo apt-get install -y redis-server ;;
    dnf|yum) sudo $PM install -y redis ;;
  esac

  echo "[2/4] enabling redis..."
  sudo systemctl enable --now redis-server 2>/dev/null \
    || sudo systemctl enable --now redis 2>/dev/null \
    || echo "  warn: could not enable via systemctl, start manually if needed"

  echo "[3/4] redis ping..."
  if redis-cli ping 2>/dev/null | grep -q PONG; then
    echo "  PONG"
  else
    # 已配 requirepass 时无密码 ping 会 NOAUTH,不算硬失败
    echo "  warn: redis-cli ping 没 PONG(可能已配 requirepass,跳过)"
  fi
fi

echo "[4/4] installing juicefs client..."
if command -v juicefs >/dev/null; then
  echo "  already installed: $(juicefs version | head -1)"
else
  curl -sSL https://d.juicefs.com/install | sudo sh -
  juicefs version
fi

echo
if [[ "$JFS_CLIENT_ONLY" == "1" ]]; then
  echo "DONE (client-only)。下一步:"
  echo "  1. 把 /etc/juicefs/${JFS_NAME}.env 从主节点 scp 过来"
  echo "  2. JFS_META_URL='redis://<主节点 IP>:6379/0' JFS_REDIS_LOCAL=0 \\"
  echo "       sudo -E bash 10-systemd-unit.sh"
  echo "  3. sudo systemctl start juicefs-${JFS_NAME}.service"
else
  echo "DONE. Next: ./02-prepare.sh"
fi
