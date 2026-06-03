#!/usr/bin/env bash
# 写一个 systemd service 让 JuiceFS 在开机自动挂载。
#
# - 依赖 redis-server.service (Requires + After):redis 没起来不挂,避免 EIO
# - ExecStop=juicefs umount 触发 writeback cache 刷盘,防止断电丢数据
# - 参数来自 00-config.sh,本脚本生成 unit 文件,不写死路径
#
# 幂等。可重跑覆盖。

set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh

JUICEFS_BIN="$(command -v juicefs)"
[[ -x "$JUICEFS_BIN" ]] || { echo "ERROR: juicefs binary not found in PATH" >&2; exit 1; }

UNIT_NAME="juicefs-${JFS_NAME}.service"
UNIT_PATH="/etc/systemd/system/$UNIT_NAME"

echo "[1/4] 渲染 unit -> $UNIT_PATH"
sudo tee "$UNIT_PATH" >/dev/null <<EOF
[Unit]
Description=JuiceFS mount $JFS_MOUNT
After=network-online.target redis-server.service
Wants=network-online.target
Requires=redis-server.service

[Service]
Type=forking
ExecStartPre=/bin/mkdir -p $JFS_MOUNT
ExecStart=$JUICEFS_BIN mount \\
  --cache-dir=$JFS_CACHE_DIR \\
  --cache-size=$JFS_CACHE_SIZE_MB \\
  --writeback \\
  --background \\
  $JFS_META_URL $JFS_MOUNT
ExecStop=$JUICEFS_BIN umount $JFS_MOUNT
Restart=on-failure
RestartSec=5
TimeoutStartSec=60
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
EOF
sudo chmod 644 "$UNIT_PATH"

echo "[2/4] systemctl daemon-reload"
sudo systemctl daemon-reload

echo "[3/4] enable"
sudo systemctl enable "$UNIT_NAME"

echo "[4/4] 检查当前挂载状态"
if mountpoint -q "$JFS_MOUNT"; then
  echo "  $JFS_MOUNT 已挂载(手动挂的)。"
  echo "  要让 systemd 接管,先 umount 再 start:"
  echo "    sudo $JUICEFS_BIN umount $JFS_MOUNT"
  echo "    sudo systemctl start $UNIT_NAME"
  echo "  或下次重启自动生效,无需操作。"
else
  echo "  $JFS_MOUNT 未挂载。启动:"
  echo "    sudo systemctl start $UNIT_NAME"
fi

echo
echo "DONE. 验证:"
echo "  systemctl status $UNIT_NAME"
echo "  systemctl is-enabled $UNIT_NAME"
