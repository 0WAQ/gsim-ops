#!/usr/bin/env bash
# Install Redis (本机) + JuiceFS client. Requires sudo.

set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh

if command -v apt-get >/dev/null; then PM=apt
elif command -v dnf >/dev/null; then PM=dnf
elif command -v yum >/dev/null; then PM=yum
else echo "unsupported pkg mgr; install redis manually" >&2; exit 1
fi

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
if redis-cli ping | grep -q PONG; then
  echo "  PONG"
else
  echo "  FAIL: redis not responding" >&2; exit 1
fi

echo "[4/4] installing juicefs client..."
if command -v juicefs >/dev/null; then
  echo "  already installed: $(juicefs version | head -1)"
else
  curl -sSL https://d.juicefs.com/install | sudo sh -
  juicefs version
fi

echo
echo "DONE. Next: ./02-prepare.sh"
