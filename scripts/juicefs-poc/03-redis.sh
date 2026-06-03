#!/usr/bin/env bash
# Redis 监听 0.0.0.0 + requirepass + protected-mode yes,供 JuiceFS 跨节点访问。
# 密码存到 /etc/juicefs/<name>.env (0600 root:root),由 04-systemd.sh 通过
# EnvironmentFile 注入 META_PASSWORD,避免出现在 ps / cmdline。
# 幂等:再跑会复用已有密码。

set -euo pipefail
cd "$(dirname "$0")"
source ./_lib.sh
source ./config.sh

require_sudo
require_systemd
require_bin redis-cli "apt install redis-tools"
require_bin openssl   "apt install openssl"
systemctl list-unit-files | grep -q '^redis-server\.service' \
  || err "找不到 redis-server.service,先跑 00-install.sh"

ENV_FILE="/etc/juicefs/${JFS_NAME}.env"
REDIS_CONF="/etc/redis/redis.conf"
[[ -f "$REDIS_CONF" ]] || err "找不到 $REDIS_CONF"
MARK_BEGIN="# BEGIN juicefs-poc managed"
MARK_END="# END juicefs-poc managed"

info "[1/4] 密码 -> $ENV_FILE"
sudo install -d -m 0700 -o root -g root /etc/juicefs
if sudo test -f "$ENV_FILE"; then
  info "  已存在,复用"
else
  PASS="$(openssl rand -hex 24)"
  printf 'META_PASSWORD=%s\n' "$PASS" | sudo tee "$ENV_FILE" >/dev/null
  sudo chmod 0600 "$ENV_FILE"
  sudo chown root:root "$ENV_FILE"
  unset PASS
  info "  新建 (0600 root:root)"
fi

# 改 redis 前必须停 juicefs,否则现挂载在 AUTH 切换瞬间全 EIO
JFS_SVC="juicefs-${JFS_NAME}.service"
JFS_WAS_ACTIVE=0
if systemctl is-active --quiet "$JFS_SVC"; then
  JFS_WAS_ACTIVE=1
  info "  $JFS_SVC 在跑,先停掉避开 AUTH 中断"
  sudo systemctl stop "$JFS_SVC"
fi

info "[2/4] 改 $REDIS_CONF"
sudo sed -i.bak "/^${MARK_BEGIN}/,/^${MARK_END}/d" "$REDIS_CONF"
PASS_VAL="$(sudo grep -E '^META_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"
sudo tee -a "$REDIS_CONF" >/dev/null <<EOF
$MARK_BEGIN
bind 0.0.0.0 -::1
protected-mode yes
requirepass $PASS_VAL
$MARK_END
EOF
unset PASS_VAL

info "[3/4] restart redis-server"
sudo systemctl restart redis-server
sleep 1
systemctl is-active --quiet redis-server || err "redis-server 没起来"

info "[4/4] AUTH self-test"
sudo bash -c ". $ENV_FILE && redis-cli -h 127.0.0.1 -a \"\$META_PASSWORD\" ping" 2>/dev/null \
  | grep -q PONG || err "AUTH 失败"
info "  127.0.0.1 PONG"
ss -tlnp 2>/dev/null | grep -E ':6379\s' | head -3 || true

echo
if (( JFS_WAS_ACTIVE )); then
  warn "juicefs 之前在跑,需要重新渲染 unit + start:"
  warn "  sudo -E bash 04-systemd.sh"
  warn "  sudo systemctl start $JFS_SVC"
else
  info "DONE. 下一步: sudo -E bash 04-systemd.sh"
fi
