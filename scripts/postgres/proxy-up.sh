#!/usr/bin/env bash
# 一次性: 给 dockerd 配代理 -> 拉 postgres:17 -> retag -> 起 ops-pg 容器
# 用完记得跑 proxy-teardown.sh 撤掉 daemon 代理
# 需 sudo (会提示输密码), 会重启 docker (node_exporter 自动拉回)
set -euo pipefail

PROXY="http://10.9.100.145:1080"
DROPIN=/etc/systemd/system/docker.service.d/http-proxy.conf
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "=== [1/4] 配 dockerd 代理 drop-in ==="
sudo mkdir -p /etc/systemd/system/docker.service.d
sudo tee "$DROPIN" >/dev/null <<EOF
# 临时: 为拉镜像配代理; 撤销跑 proxy-teardown.sh
[Service]
Environment="HTTP_PROXY=$PROXY"
Environment="HTTPS_PROXY=$PROXY"
Environment="NO_PROXY=localhost,127.0.0.1,10.0.0.0/8,192.168.0.0/16,.local"
EOF
sudo systemctl daemon-reload
sudo systemctl restart docker
sleep 3
sudo systemctl show docker --property=Environment | grep -q HTTP_PROXY \
  && echo "  proxy 生效 ✓" || { echo "  proxy 未生效, 中止"; exit 1; }

echo "=== [2/4] 拉 postgres:17 ==="
docker pull postgres:17

echo "=== [3/4] 起 ops-pg 容器 ==="
cd "$HERE"
docker compose up -d
sleep 8

echo "=== [4/4] 状态 ==="
docker compose ps
echo ""
echo "完成. 记得跑: ./proxy-teardown.sh 撤掉 daemon 代理"
