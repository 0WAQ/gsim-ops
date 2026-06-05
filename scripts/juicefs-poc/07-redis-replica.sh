#!/usr/bin/env bash
# 在 replica 节点(默认 150)上起 redis-jfs:6380 实例,作为 160 master 的 replica。
# 部署后由 Sentinel(08-sentinel.sh)接管 master 选举;本脚本只负责拉起 redis 实例 + 复制配置。
#
# 用法:
#   sudo -E bash 07-redis-replica.sh                 # MASTER_HOST 默认 10.9.100.160
#   MASTER_HOST=10.9.100.160 sudo -E bash 07-redis-replica.sh
#
# 前置:
#   1. 当前主机能 TCP 连上 master:6380 (网络通)
#   2. master 的 /etc/juicefs/<JFS_NAME>-jfs.env (META_PASSWORD=...) 已拷贝到本机
#      → 推荐做法:在 master 上 `sudo cat /etc/juicefs/alphalib-jfs.env` 拿到值后,
#         在本机 `sudo bash -c 'echo "META_PASSWORD=..." > /etc/juicefs/alphalib-jfs.env'`
#      本脚本如发现 env 文件缺失或不可读会显式报错
#
# 幂等:重跑会复用 conf 和密码,只确认服务状态 + replication 状态。

set -euo pipefail
cd "$(dirname "$0")"
source ./_lib.sh
source ./config.sh

require_sudo
require_systemd
require_bin redis-server "apt install redis-server"
require_bin redis-cli    "apt install redis-tools"

# ============================================================
# 配置
# ============================================================
MASTER_HOST="${MASTER_HOST:-10.9.100.160}"
MASTER_PORT=6380
REPL_PORT=6380
NAME=redis-jfs
DATA_DIR=/var/lib/${NAME}
CONF_DIR=/etc/${NAME}
CONF=${CONF_DIR}/redis.conf
UNIT=/etc/systemd/system/${NAME}.service
SVC=${NAME}.service
ENV_FILE=/etc/juicefs/${JFS_NAME}-jfs.env

# ============================================================
# Pre-flight
# ============================================================
info "[1/7] pre-flight"

# master TCP 必须通(否则 replica 起来也连不上)
require_tcp "$MASTER_HOST" "$MASTER_PORT" 5
info "  master $MASTER_HOST:$MASTER_PORT 可达"

# env 文件必须有 (本节点没法自己生成密码,必须 = master 那边的密码)
if ! sudo test -r "$ENV_FILE"; then
  err "$ENV_FILE 缺失或不可读。\n  请在 master 上 'sudo cat $ENV_FILE' 拿 META_PASSWORD=... 然后:\n    sudo install -d -m 0700 /etc/juicefs\n    sudo bash -c \"echo 'META_PASSWORD=<value>' > $ENV_FILE\"\n    sudo chmod 0600 $ENV_FILE && sudo chown root:root $ENV_FILE"
fi
PASS_VAL="$(sudo grep -E '^META_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"
[[ -z "$PASS_VAL" ]] && err "$ENV_FILE 里 META_PASSWORD 为空"
info "  $ENV_FILE OK"

# master AUTH self-test (确认拿到的密码对)
redis-cli -h "$MASTER_HOST" -p "$MASTER_PORT" -a "$PASS_VAL" --no-auth-warning ping 2>/dev/null \
  | grep -q PONG || err "master AUTH 失败 -- $ENV_FILE 的密码跟 master 不一致"
info "  master AUTH 通过"

# 本机 6380 不能被别的进程占
if ss -tln 2>/dev/null | grep -q ":${REPL_PORT}\s"; then
  if systemctl is-active --quiet "$SVC" 2>/dev/null; then
    info "  $SVC 已在,后续验证状态"
  else
    err "本机 $REPL_PORT 被其它进程占用 (非 $SVC)"
  fi
fi

# ============================================================
# 目录 + conf
# ============================================================
info "[2/7] 数据目录 $DATA_DIR (redis:redis 0750)"
sudo install -d -m 0750 -o redis -g redis "$DATA_DIR"

info "[3/7] conf 目录 $CONF_DIR"
sudo install -d -m 0755 -o root -g root "$CONF_DIR"

info "[4/7] 渲染 $CONF (replica of $MASTER_HOST:$MASTER_PORT)"
sudo tee "$CONF" >/dev/null <<EOF
# Managed by 07-redis-replica.sh -- DO NOT hand edit, redis 'config rewrite' will overwrite.
# Replica of $MASTER_HOST:$MASTER_PORT. JuiceFS metadata 专用。

# 网络 (跨节点,sentinel 可达)
bind 0.0.0.0 -::1
port ${REPL_PORT}
protected-mode yes

# Replica
replicaof ${MASTER_HOST} ${MASTER_PORT}
masterauth ${PASS_VAL}
replica-read-only yes

# 持久化 (replica 也持久化,机器挂了再起能少做一次 full sync)
dir ${DATA_DIR}
dbfilename dump.rdb
appendonly yes
appendfilename "appendonly.aof"
appenddirname "appendonlydir"
appendfsync everysec
save 3600 1
save 300 100
save 60 10000

# AUTH (sentinel 会用 auth-pass 来 talk to replica;client 直接连 replica 不应该走这条路)
requirepass ${PASS_VAL}

# JuiceFS 元数据全在 redis, 内存不能 evict
maxmemory-policy noeviction

logfile ""
loglevel notice
EOF
sudo chmod 0640 "$CONF"
sudo chown root:redis "$CONF"
unset PASS_VAL
info "  $CONF (0640 root:redis)"

# ============================================================
# systemd unit
# ============================================================
info "[5/7] 渲染 $UNIT (复用 ubuntu redis 默认 hardening)"
sudo tee "$UNIT" >/dev/null <<EOF
[Unit]
Description=Redis JuiceFS metadata replica (port ${REPL_PORT}, master ${MASTER_HOST}:${MASTER_PORT})
After=network.target
Documentation=http://redis.io/documentation, man:redis-server(1)

[Service]
Type=notify
ExecStart=/usr/bin/redis-server ${CONF} --supervised systemd --daemonize no
TimeoutStopSec=0
Restart=always
User=redis
Group=redis
RuntimeDirectory=${NAME}
RuntimeDirectoryMode=2755

UMask=007
PrivateTmp=true
LimitNOFILE=65535
PrivateDevices=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=-${DATA_DIR}
ReadWritePaths=-${CONF_DIR}

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
info "  $UNIT 渲染完成"

# ============================================================
# 启动 + 验证
# ============================================================
info "[6/7] start $SVC"
sudo systemctl enable --now "$SVC"
sleep 3
systemctl is-active --quiet "$SVC" || {
  journalctl -u "$SVC" --no-pager -n 30
  err "$SVC 没起来"
}
info "  $SVC active"

info "[7/7] 验证 replication"
PASS_VAL="$(sudo grep -E '^META_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"

# 本机 6380 自身 PING
redis-cli -h 127.0.0.1 -p $REPL_PORT -a "$PASS_VAL" --no-auth-warning ping 2>/dev/null \
  | grep -q PONG || err "本机 6380 AUTH 失败"
info "  127.0.0.1:$REPL_PORT PONG"

# replication role
ROLE=$(redis-cli -h 127.0.0.1 -p $REPL_PORT -a "$PASS_VAL" --no-auth-warning info replication 2>/dev/null \
       | grep '^role:' | cut -d: -f2 | tr -d '\r')
info "  本机 role: $ROLE"
[[ "$ROLE" == "slave" ]] || err "本机 role 不是 slave (是 $ROLE),检查 conf 里 replicaof"

# master 视角 connected_slaves
sleep 2
MASTER_INFO=$(redis-cli -h "$MASTER_HOST" -p "$MASTER_PORT" -a "$PASS_VAL" --no-auth-warning info replication 2>/dev/null)
CONNECTED=$(echo "$MASTER_INFO" | grep '^connected_slaves:' | cut -d: -f2 | tr -d '\r')
info "  master.connected_slaves = $CONNECTED"
[[ "$CONNECTED" -ge 1 ]] || warn "master 还没看见 replica (可能还在 handshake)"

# 看 master 上是否能枚举到自己
echo "$MASTER_INFO" | grep '^slave[0-9]:' | sed 's/^/    /'

# dbsize 对照 (replication 完成后应一致)
DB_M=$(redis-cli -h "$MASTER_HOST" -p "$MASTER_PORT" -a "$PASS_VAL" --no-auth-warning dbsize 2>/dev/null)
DB_R=$(redis-cli -h 127.0.0.1 -p $REPL_PORT -a "$PASS_VAL" --no-auth-warning dbsize 2>/dev/null)
info "  dbsize: master=$DB_M  replica=$DB_R"
unset PASS_VAL

if [[ "$DB_M" == "$DB_R" ]]; then
  info "  ✓ 数据已同步"
else
  warn "  dbsize 不一致 (可能 full sync 还在进行,稍后 redis-cli ... info replication 看 master_sync_in_progress)"
fi

echo
info "DONE."
echo
info "  - 实例: pid=$(systemctl show $SVC -p MainPID --value)"
info "  - 数据 dir: $DATA_DIR"
info "  - conf:    $CONF"
info "  - 密码 env: $ENV_FILE"
info "  - master:  $MASTER_HOST:$MASTER_PORT"
echo
info "下一步: 在 160/150/144 上跑 sudo -E bash 08-sentinel.sh"
