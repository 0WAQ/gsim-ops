#!/usr/bin/env bash
# 撤销 dockerd 代理 (proxy-up.sh 的逆操作)
# 删 drop-in + reload + restart docker. ops-pg / node_exporter 自动拉回.
set -euo pipefail

DROPIN=/etc/systemd/system/docker.service.d/http-proxy.conf

echo "=== 删代理 drop-in ==="
sudo rm -f "$DROPIN"
# 若目录空了一并删掉, 恢复原始状态 (原本无此目录)
sudo rmdir --ignore-fail-on-non-empty /etc/systemd/system/docker.service.d 2>/dev/null || true

echo "=== reload + restart docker ==="
sudo systemctl daemon-reload
sudo systemctl restart docker
sleep 3

echo "=== 确认 proxy 已撤 (应无输出) ==="
sudo systemctl show docker --property=Environment | grep HTTP_PROXY && echo "!! 仍有 proxy" || echo "  已撤干净 ✓"
echo "=== 容器状态 ==="
docker ps --format '{{.Names}}\t{{.Status}}'
