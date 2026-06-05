#!/usr/bin/env bash
# 在节点上起 redis-sentinel-jfs:26380,监控 redis-jfs master + 自动 failover。
#
# 用法 (每个 sentinel 节点都跑一次,本脚本幂等):
#   sudo -E bash 08-sentinel.sh                                # MASTER_HOST 默认 10.9.100.160
#   MASTER_HOST=10.9.100.160 sudo -E bash 08-sentinel.sh
#
# 前置:
#   1. master (默认 160) 已经在跑 redis-jfs:6380
#   2. 本机 /etc/juicefs/<JFS_NAME>-jfs.env (META_PASSWORD=...) 已经存在,跟 master 同密码
#   3. 至少有一台 replica 已经跑起来(可以不强制,但 sentinel 起来前还没 replica 则 failover 无意义)
#
# 拓扑(推荐):3 个 sentinel 分散在不同机器
#   160 (master)  + sentinel-① :26380
#   150 (replica) + sentinel-② :26380
#   144 (client)  + sentinel-③ :26380   ← 投票 only,144 跨段没关系
#
# quorum=2:3 个 sentinel 中 2 个达成共识就发起 failover。
# 任何 1 台机器全挂仍能 failover;2 台同时挂则 quorum 达不到 → 服务中断但数据安全。

set -euo pipefail
cd "$(dirname "$0")"
source ./_lib.sh
source ./config.sh

require_sudo
require_systemd
require_bin redis-sentinel "apt install redis-sentinel"
require_bin redis-cli      "apt install redis-tools"

# ============================================================
# 配置
# ============================================================
MASTER_HOST="${MASTER_HOST:-10.9.100.160}"
MASTER_PORT=6380
MASTER_NAME=mymaster
SENTINEL_PORT=26380
QUORUM=2
DOWN_AFTER_MS=5000
FAILOVER_TIMEOUT_MS=10000

NAME=redis-sentinel-jfs
DATA_DIR=/var/lib/${NAME}
CONF_DIR=/etc/${NAME}
CONF=${CONF_DIR}/sentinel.conf
UNIT=/etc/systemd/system/${NAME}.service
SVC=${NAME}.service
ENV_FILE=/etc/juicefs/${JFS_NAME}-jfs.env

# ============================================================
# Pre-flight
# ============================================================
info "[1/6] pre-flight"

if ! sudo test -r "$ENV_FILE"; then
  err "$ENV_FILE 缺失或不可读。需要 master 的 META_PASSWORD。\n  从 master 拷:sudo scp <master>:$ENV_FILE $ENV_FILE && sudo chmod 600 $ENV_FILE"
fi
PASS_VAL="$(sudo grep -E '^META_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"
[[ -z "$PASS_VAL" ]] && err "$ENV_FILE 里 META_PASSWORD 为空"

# 验证密码对得上 master
require_tcp "$MASTER_HOST" "$MASTER_PORT" 5
redis-cli -h "$MASTER_HOST" -p "$MASTER_PORT" -a "$PASS_VAL" --no-auth-warning ping 2>/dev/null \
  | grep -q PONG || err "用 $ENV_FILE 的密码 ping master 失败"
info "  master $MASTER_HOST:$MASTER_PORT AUTH OK"

# 本机 26380 不能被别的占
if ss -tln 2>/dev/null | grep -q ":${SENTINEL_PORT}\s"; then
  if systemctl is-active --quiet "$SVC" 2>/dev/null; then
    info "  $SVC 已在,后续验证状态"
  else
    err "本机 $SENTINEL_PORT 被其它进程占用 (非 $SVC)"
  fi
fi

# ============================================================
# 目录 + conf
# ============================================================
info "[2/6] 数据目录 $DATA_DIR (redis:redis 0750)"
# Sentinel 会改自己的 conf,所以 conf 也要写权限给 redis
sudo install -d -m 0750 -o redis -g redis "$DATA_DIR"

info "[3/6] conf 目录 $CONF_DIR"
sudo install -d -m 0750 -o redis -g redis "$CONF_DIR"

info "[4/6] 渲染 $CONF"
# 注意: sentinel 会自动 rewrite conf 加上 myid / known-sentinel / known-replica 等条目。
# 重跑本脚本时,如果 conf 已存在 + sentinel.service active,就不覆盖避免清掉运行时状态;
# 让 sentinel 通过 SENTINEL MONITOR 命令更新参数(更安全)。
if sudo test -f "$CONF" && systemctl is-active --quiet "$SVC" 2>/dev/null; then
  info "  $CONF 已存在 + $SVC 运行中,跳过 conf 覆盖(避免清掉 sentinel 已发现的同伴)"
else
  sudo tee "$CONF" >/dev/null <<EOF
# Managed by 08-sentinel.sh on initial bootstrap.
# Sentinel 启动后会 rewrite 这个文件加上 myid / known-* 等运行时状态。
# 修改静态参数请改本脚本然后:
#   sudo systemctl stop ${SVC}
#   sudo rm $CONF
#   sudo -E bash 08-sentinel.sh

port ${SENTINEL_PORT}
bind 0.0.0.0 -::1
protected-mode no

dir ${DATA_DIR}

# 监控的 master:  sentinel monitor <name> <host> <port> <quorum>
sentinel monitor ${MASTER_NAME} ${MASTER_HOST} ${MASTER_PORT} ${QUORUM}

# 鉴权 (master / replica 都需要相同密码)
sentinel auth-pass ${MASTER_NAME} ${PASS_VAL}

# 时间参数
sentinel down-after-milliseconds ${MASTER_NAME} ${DOWN_AFTER_MS}
sentinel failover-timeout ${MASTER_NAME} ${FAILOVER_TIMEOUT_MS}
sentinel parallel-syncs ${MASTER_NAME} 1

# 不向 master 发广播 hello (默认即可,保留这条提醒自己默认值)
# sentinel deny-scripts-reconfig yes

logfile ""
loglevel notice
EOF
  sudo chmod 0660 "$CONF"
  sudo chown redis:redis "$CONF"
  info "  $CONF (0660 redis:redis -- sentinel 需要写)"
fi
unset PASS_VAL

# ============================================================
# systemd unit
# ============================================================
info "[5/6] 渲染 $UNIT"
sudo tee "$UNIT" >/dev/null <<EOF
[Unit]
Description=Redis Sentinel for redis-jfs (${MASTER_NAME}, port ${SENTINEL_PORT})
After=network.target
Documentation=http://redis.io/topics/sentinel

[Service]
Type=notify
# sentinel 模式: redis-sentinel 是 redis-server 的别名,加 --sentinel
ExecStart=/usr/bin/redis-sentinel ${CONF} --supervised systemd --daemonize no
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
ExecPaths=/usr/bin/redis-sentinel /usr/bin/redis-server /usr/lib /lib

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
info "  $UNIT 渲染完成"

# ============================================================
# 启动 + 验证
# ============================================================
info "[6/6] start $SVC + 验证"
sudo systemctl enable --now "$SVC"
sleep 3
systemctl is-active --quiet "$SVC" || {
  journalctl -u "$SVC" --no-pager -n 30
  err "$SVC 没起来"
}
info "  $SVC active"

# Sentinel 操作不需要 AUTH(默认配置),直接查
SENTINEL_MASTERS=$(redis-cli -h 127.0.0.1 -p $SENTINEL_PORT sentinel masters 2>/dev/null)
if [[ -z "$SENTINEL_MASTERS" ]]; then
  warn "  sentinel masters 输出为空 (可能还在 discovery)"
else
  # 解析 master state
  STATE=$(echo "$SENTINEL_MASTERS" | awk '/^flags$/ {getline; print; exit}')
  IP=$(echo "$SENTINEL_MASTERS" | awk '/^ip$/ {getline; print; exit}')
  PORT=$(echo "$SENTINEL_MASTERS" | awk '/^port$/ {getline; print; exit}')
  info "  monitored master: $IP:$PORT  flags=$STATE"
  echo "$SENTINEL_MASTERS" | head -40 | sed 's/^/    /'
fi

# 已知 sentinels 和 replicas
KNOWN_SENTINELS=$(redis-cli -h 127.0.0.1 -p $SENTINEL_PORT sentinel sentinels ${MASTER_NAME} 2>/dev/null | grep -c '^name$' || true)
KNOWN_REPLICAS=$(redis-cli -h 127.0.0.1 -p $SENTINEL_PORT sentinel replicas ${MASTER_NAME} 2>/dev/null | grep -c '^name$' || true)
info "  本 sentinel 看到 $KNOWN_SENTINELS 个其它 sentinel, $KNOWN_REPLICAS 个 replica"

echo
info "DONE."
echo
info "  - 实例: pid=$(systemctl show $SVC -p MainPID --value)"
info "  - conf: $CONF (sentinel 会 rewrite 加运行时状态)"
info "  - port: $SENTINEL_PORT"
info "  - master: $MASTER_HOST:$MASTER_PORT name=$MASTER_NAME quorum=$QUORUM"
echo
info "  在所有 sentinel 都起来后 (3 台),互查应该都看到 +2 个其它 sentinel:"
info "    redis-cli -p $SENTINEL_PORT sentinel sentinels $MASTER_NAME | grep -c '^name$'"
info ""
info "  failover 演练:"
info "    sudo redis-cli -p $SENTINEL_PORT sentinel failover $MASTER_NAME   # 手动触发"
info "    sudo systemctl stop redis-jfs    # 在 master 上, 模拟挂掉 (10s 内自动 failover)"
echo
info "下一步: 三个 sentinel 都起来后,跑 09-switch-meta-url.sh 把客户端切到 redis-sentinel:// URL"
