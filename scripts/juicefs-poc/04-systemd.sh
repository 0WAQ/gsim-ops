#!/usr/bin/env bash
# 渲染 juicefs-<name>.service 让 JuiceFS 开机自动挂载。
#
# - 依赖 redis-server.service (Requires + After): redis 没起来不挂,避免 EIO
# - JFS_REDIS_LOCAL=0 关掉这条依赖(client 节点的 redis 在远端)
# - 如果 /etc/juicefs/<name>.env 存在,通过 EnvironmentFile 注入 META_PASSWORD,
#   URL 里不带密码(防 ps 泄露)
# - ExecStop=juicefs umount 触发 writeback cache 刷盘,防断电丢数据
#
# 幂等,可重跑覆盖。

set -euo pipefail
cd "$(dirname "$0")"
source ./_lib.sh
source ./config.sh

require_sudo
require_systemd
require_bin juicefs "curl -sSL https://d.juicefs.com/install | sudo sh -"

JUICEFS_BIN="$(command -v juicefs)"
UNIT_NAME="juicefs-${JFS_NAME}.service"
UNIT_PATH="/etc/systemd/system/$UNIT_NAME"
ENV_FILE="/etc/juicefs/${JFS_NAME}.env"

# 跨节点:JFS_REDIS_LOCAL=0 让 client 不依赖本地 redis-server
JFS_REDIS_LOCAL="${JFS_REDIS_LOCAL:-1}"
REQ_LINE=""
AFTER_LINE="After=network-online.target"
if [[ "$JFS_REDIS_LOCAL" == "1" ]]; then
  REQ_LINE="Requires=redis-server.service"
  AFTER_LINE="After=network-online.target redis-server.service"
fi

ENV_LINE=""
if sudo test -f "$ENV_FILE"; then
  ENV_LINE="EnvironmentFile=$ENV_FILE"
  info "  检测到 $ENV_FILE, 通过 EnvironmentFile 注入 META_PASSWORD"
fi

info "[1/3] 渲染 -> $UNIT_PATH"
sudo tee "$UNIT_PATH" >/dev/null <<EOF
[Unit]
Description=JuiceFS mount $JFS_MOUNT
$AFTER_LINE
Wants=network-online.target
$REQ_LINE

[Service]
Type=forking
$ENV_LINE
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

info "[2/3] daemon-reload + enable"
sudo systemctl daemon-reload
sudo systemctl enable "$UNIT_NAME" >/dev/null

info "[3/3] 当前状态"
if mountpoint -q "$JFS_MOUNT"; then
  info "  $JFS_MOUNT 已挂载(手动挂的)。systemd 接管需:"
  info "    sudo $JUICEFS_BIN umount $JFS_MOUNT"
  info "    sudo systemctl start $UNIT_NAME"
else
  info "  $JFS_MOUNT 未挂载。启动:"
  info "    sudo systemctl start $UNIT_NAME"
fi

echo
info "DONE."
info "  systemctl status $UNIT_NAME"
info "  systemctl is-enabled $UNIT_NAME"
