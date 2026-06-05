#!/usr/bin/env bash
# 起独立的 redis 实例给 JuiceFS 专用 (port 6380),与业务侧 6379 完全隔离。
#
# 第一步:只起实例,不迁数据,不改 config.sh / juicefs unit。
# 验过能起、AOF on、跨进程隔离 (kill 6379 不影响 6380, 反之亦然)。
#
# 第二步 (06-meta-migrate.sh):MIGRATE 69111 keys 6379->6380, 改 config.sh, 重启 unit。
#
# 用法: sudo -E bash 06-redis-jfs.sh
# 幂等:再跑会复用已有密码 + 已有 conf,验证 AOF 状态。

set -euo pipefail
cd "$(dirname "$0")"
source ./_lib.sh
source ./config.sh

require_sudo
require_systemd
require_bin redis-server "apt install redis-server"
require_bin redis-cli    "apt install redis-tools"
require_bin openssl

# ============================================================
# 配置 (硬编码 -- PoC 阶段不需要做成可配)
# ============================================================
NEW_PORT=6380
NEW_NAME=redis-jfs
NEW_DIR=/var/lib/${NEW_NAME}
NEW_CONF_DIR=/etc/${NEW_NAME}
NEW_CONF=${NEW_CONF_DIR}/redis.conf
NEW_UNIT=/etc/systemd/system/${NEW_NAME}.service
NEW_SVC=${NEW_NAME}.service
NEW_ENV=/etc/juicefs/${JFS_NAME}-jfs.env       # 给后续 06-meta-migrate / juicefs client 用
ALPHA_BIZ_ENV=/etc/juicefs/${JFS_NAME}.env     # 这个还是 6379 的密码,先不动

# 已有 6379 占着不能复用
ss -tln 2>/dev/null | grep -q ':6380\s' && info "  6380 已被占 (可能上次跑过本脚本)" || true
[[ -d "$NEW_DIR" ]] && info "  $NEW_DIR 已存在"

info "[1/6] 创建数据目录 $NEW_DIR (redis:redis 0750)"
sudo install -d -m 0750 -o redis -g redis "$NEW_DIR"

info "[2/6] 创建 conf 目录 $NEW_CONF_DIR"
sudo install -d -m 0755 -o root -g root "$NEW_CONF_DIR"

info "[3/6] 密码 -> $NEW_ENV"
sudo install -d -m 0700 -o root -g root /etc/juicefs
if sudo test -f "$NEW_ENV"; then
  info "  已存在,复用"
else
  PASS_NEW="$(openssl rand -hex 24)"
  printf 'META_PASSWORD=%s\n' "$PASS_NEW" | sudo tee "$NEW_ENV" >/dev/null
  sudo chmod 0600 "$NEW_ENV"
  sudo chown root:root "$NEW_ENV"
  unset PASS_NEW
  info "  新建 (0600 root:root)"
fi
PASS_VAL="$(sudo grep -E '^META_PASSWORD=' "$NEW_ENV" | cut -d= -f2-)"

info "[4/6] 渲染 $NEW_CONF (AOF 一开始就 on, 避开'空 AOF 优先 RDB'雷)"
sudo tee "$NEW_CONF" >/dev/null <<EOF
# Managed by 06-redis-jfs.sh -- DO NOT hand edit, redis 'config rewrite' will overwrite.
# 独立 redis 实例,JuiceFS 专用。

# 网络 (跨节点访问)
bind 0.0.0.0 -::1
port ${NEW_PORT}
protected-mode yes

# 持久化
dir ${NEW_DIR}
dbfilename dump.rdb
appendonly yes
appendfilename "appendonly.aof"
appenddirname "appendonlydir"
appendfsync everysec
# RDB 默认 save 触发器
save 3600 1
save 300 100
save 60 10000

# AUTH (写在 conf 里, conf mode 0640 redis:redis 隔开非 redis 用户)
requirepass ${PASS_VAL}
# masterauth = requirepass: 此节点 failover 后变 replica 时需要它去连新 master。
# Sentinel 会自动写 replicaof <new_master> + config rewrite,但不会补 masterauth,
# 必须预先在 conf 里 (master_link_status=down 的典型坑)。
masterauth ${PASS_VAL}

# JuiceFS 元数据全都在 redis, 内存不能 evict
maxmemory-policy noeviction

# logging via journal (Type=notify, no logfile)
logfile ""
loglevel notice
EOF
sudo chmod 0640 "$NEW_CONF"
sudo chown root:redis "$NEW_CONF"
unset PASS_VAL
info "  $NEW_CONF (0640 root:redis)"

info "[5/6] 渲染 $NEW_UNIT (复用 ubuntu redis 默认 hardening)"
sudo tee "$NEW_UNIT" >/dev/null <<EOF
[Unit]
Description=Redis dedicated instance for JuiceFS metadata (port ${NEW_PORT})
After=network.target
Documentation=http://redis.io/documentation, man:redis-server(1)

[Service]
Type=notify
ExecStart=/usr/bin/redis-server ${NEW_CONF} --supervised systemd --daemonize no
TimeoutStopSec=0
Restart=always
User=redis
Group=redis
RuntimeDirectory=${NEW_NAME}
RuntimeDirectoryMode=2755

UMask=007
PrivateTmp=true
LimitNOFILE=65535
PrivateDevices=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=-${NEW_DIR}
ReadWritePaths=-${NEW_CONF_DIR}

CapabilityBoundingSet=
LockPersonality=true
MemoryDenyWriteExecute=true
NoNewPrivileges=true
PrivateUsers=true
ProtectClock=true
ProtectControlGroups=true
ProtectHostname=true
ProtectKernelLogs=true
ProtectKernelModules=true
ProtectKernelTunables=true
ProtectProc=invisible
RemoveIPC=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
RestrictNamespaces=true
RestrictRealtime=true
RestrictSUIDSGID=true
SystemCallArchitectures=native
SystemCallFilter=@system-service
SystemCallFilter=~ @privileged @resources

NoExecPaths=/
ExecPaths=/usr/bin/redis-server /usr/lib /lib

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
info "  $NEW_UNIT 渲染完成"

info "[6/6] start ${NEW_SVC} + AUTH self-test"
sudo systemctl enable --now "$NEW_SVC"
sleep 2
systemctl is-active --quiet "$NEW_SVC" || {
  journalctl -u "$NEW_SVC" --no-pager -n 30
  err "$NEW_SVC 没起来"
}

# AUTH 测试
PASS_VAL="$(sudo grep -E '^META_PASSWORD=' "$NEW_ENV" | cut -d= -f2-)"
redis-cli -h 127.0.0.1 -p $NEW_PORT -a "$PASS_VAL" --no-auth-warning ping 2>/dev/null \
  | grep -q PONG || err "AUTH 失败"
info "  127.0.0.1:${NEW_PORT} PONG"

# 验 AOF / dbsize / 隔离性
DBSIZE=$(redis-cli -h 127.0.0.1 -p $NEW_PORT -a "$PASS_VAL" --no-auth-warning dbsize 2>/dev/null)
AOF=$(redis-cli -h 127.0.0.1 -p $NEW_PORT -a "$PASS_VAL" --no-auth-warning config get appendonly 2>/dev/null | tail -1 | tr -d '\r')
info "  dbsize=$DBSIZE  appendonly=$AOF"
[[ "$AOF" == "yes" ]] || err "AOF 没开"
[[ "$DBSIZE" == "0" ]] || warn "dbsize 不为 0?(应该是空实例)"

# 监听端口
ss -tlnp 2>/dev/null | grep -E ":${NEW_PORT}\s" | head -3 || true

unset PASS_VAL

echo
info "DONE."
echo
info "  - 实例独立: pid=$(systemctl show $NEW_SVC -p MainPID --value)"
info "  - 数据 dir: $NEW_DIR"
info "  - conf:    $NEW_CONF"
info "  - 密码 env: $NEW_ENV (给后续 juicefs unit 用)"
info "  - 6379 业务侧 redis 完全没动: $ALPHA_BIZ_ENV 不变"
echo
info "下一步: 验证隔离性 (kill 6379 不影响 6380):"
info "  sudo systemctl restart redis-server.service"
info "  redis-cli -p 6380 -a \"\$(sudo grep -oP 'META_PASSWORD=\\K.*' $NEW_ENV)\" ping  # 应该秒回 PONG"
info ""
info "确认稳定后跑第二步: sudo -E bash 06-meta-migrate.sh (待写)"
