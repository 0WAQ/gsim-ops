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
# JFS_ENV_FILE 覆盖默认 (06-meta-migrate.sh 切到独立 redis 实例后, 密码在
# /etc/juicefs/<name>-jfs.env)
ENV_FILE="${JFS_ENV_FILE:-/etc/juicefs/${JFS_NAME}.env}"

# 跨节点:JFS_REDIS_LOCAL=0 让 client 不依赖本地 redis
# JFS_REDIS_UNIT 决定排序的本地 redis unit:
#   未设 (默认) -> redis-server.service (业务侧 6379)
#   redis-jfs   -> redis-jfs.service (06-meta-migrate 之后的独立实例 6380)
#
# 重要: Sentinel HA 部署 (B-8 之后) 下,JFS client 通过 sentinel 找远端 master,
# 本机 redis-jfs 即便挂掉(降级 replica / OOM / 升级)也不应拖垮 JFS unit。
# 因此用 Wants= 而非 Requires=:
#   - Wants 保留启动顺序提示 (开机先起 redis 再起 JFS,减少首次连接重试)
#   - 不级联 stop:本机 redis stop -> JFS unit 不被带停
# 如果还是单 redis 时代 (无 sentinel),需要 hard dep,把 Wants 改回 Requires。
JFS_REDIS_LOCAL="${JFS_REDIS_LOCAL:-1}"
JFS_REDIS_UNIT="${JFS_REDIS_UNIT:-redis-server.service}"
REQ_LINE=""
AFTER_LINE="After=network-online.target"
if [[ "$JFS_REDIS_LOCAL" == "1" ]]; then
  REQ_LINE="Wants=$JFS_REDIS_UNIT"
  AFTER_LINE="After=network-online.target $JFS_REDIS_UNIT"
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
# 三级 fallback: 标准 umount → fusermount lazy → umount -l
# 防有进程持有 mount 时卡 deactivating。前两步失败也继续 (- 前缀 + bash || 链)。
ExecStop=/bin/bash -c '$JUICEFS_BIN umount $JFS_MOUNT 2>/dev/null || /bin/fusermount -uz $JFS_MOUNT 2>/dev/null || /bin/umount -l $JFS_MOUNT 2>/dev/null || true'
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
