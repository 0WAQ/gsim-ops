#!/usr/bin/env bash
# 写一个 systemd service 让 JuiceFS 在开机自动挂载。
#
# - 依赖 redis-server.service (Requires + After):redis 没起来不挂,避免 EIO
# - ExecStop=juicefs umount 触发 writeback cache 刷盘,防止断电丢数据
# - 如果 /etc/juicefs/alphalib.env 存在,通过 EnvironmentFile 注入 META_PASSWORD,
#   URL 里不带密码(防止 ps 泄露)
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
ENV_FILE="/etc/juicefs/${JFS_NAME}.env"

# 跨节点:依赖 redis-server.service 只在 redis 本机有意义。
# 默认开,如果 JFS_META_URL 指向远端(127.0.0.1 / localhost 之外的 host),
# 用户可设 JFS_REDIS_LOCAL=0 关掉这条依赖。
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
  echo "  检测到 $ENV_FILE,将通过 EnvironmentFile 注入 META_PASSWORD"
fi

echo "[1/4] 渲染 unit -> $UNIT_PATH"
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
