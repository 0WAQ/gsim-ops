#!/usr/bin/env bash
# Redis 监听 0.0.0.0 + requirepass + protected-mode yes + AOF on,供 JuiceFS 跨节点访问。
# 密码存到 /etc/juicefs/<name>.env (0600 root:root),由 04-systemd.sh 通过
# EnvironmentFile 注入 META_PASSWORD,避免出现在 ps / cmdline。
#
# 全程运行时 (config set + config rewrite),不 restart redis -- 避开 Redis 7
# "appendonly yes 但 appendonlydir 空时,空 AOF 优先于 RDB,启动数据归零" 的雷
# (实测踩过, 详见 README "踩过的坑")。
#
# 幂等:第二次跑只对齐运行时状态,无变化时基本 no-op。

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
systemctl is-active --quiet redis-server.service \
  || err "redis-server 没在跑,先 sudo systemctl start redis-server"

ENV_FILE="/etc/juicefs/${JFS_NAME}.env"
REDIS_CONF="/etc/redis/redis.conf"
[[ -f "$REDIS_CONF" ]] || err "找不到 $REDIS_CONF"
MARK_BEGIN="# BEGIN juicefs-poc managed"
MARK_END="# END juicefs-poc managed"

JFS_SVC="juicefs-${JFS_NAME}.service"

# ============================================================
info "[1/5] 密码 -> $ENV_FILE"
# ============================================================
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
PASS_VAL="$(sudo grep -E '^META_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"

# 探测 redis 当前 auth 状态
CUR_AUTH=""
if redis-cli ping 2>/dev/null | grep -q PONG; then
  CUR_AUTH="none"
  info "  redis 当前无密码"
elif redis-cli -a "$PASS_VAL" --no-auth-warning ping 2>/dev/null | grep -q PONG; then
  CUR_AUTH="match"
  info "  redis 已有密码 (跟 env file 一致)"
else
  err "redis 已有密码但跟 $ENV_FILE 不匹配 (旧密码?). 强制 rotate 走: 先停 juicefs unit, 删 $ENV_FILE 再跑"
fi

# RC = redis-cli with auth (如果当前没密码就 fallback 不带 -a)
RC() {
  if [[ "$CUR_AUTH" == "none" ]]; then
    redis-cli "$@"
  else
    redis-cli -a "$PASS_VAL" --no-auth-warning "$@"
  fi
}

# ============================================================
# 设密码前要停 juicefs unit -- 现 connections 会在 AUTH 切换瞬间 NOAUTH 全 EIO
# 已经有密码就不需要停 (config set 别的字段不影响 AUTH)
# ============================================================
JFS_WAS_ACTIVE=0
if [[ "$CUR_AUTH" == "none" ]] && systemctl is-active --quiet "$JFS_SVC"; then
  JFS_WAS_ACTIVE=1
  info "  $JFS_SVC 在跑, 设密码前先停掉避开 AUTH 中断"
  sudo systemctl stop "$JFS_SVC"
fi

# ============================================================
info "[2/5] 清掉旧 managed block (避免重复行 / 老密码残留)"
# ============================================================
if sudo grep -q "^${MARK_BEGIN}" "$REDIS_CONF"; then
  sudo sed -i.bak "/^${MARK_BEGIN}/,/^${MARK_END}/d" "$REDIS_CONF"
  info "  已清,bak: ${REDIS_CONF}.bak"
else
  info "  无 managed block (清白状态或上次已 config rewrite 走融合写法)"
fi

# ============================================================
info "[3/5] 运行时 config set: bind / protected-mode / requirepass"
# ============================================================
# 顺序:bind/protected-mode 先,requirepass 最后
# requirepass 设完瞬间,新连接需要 -a;但已存在连接(本地这条 redis-cli)继续有效
RC config set bind '0.0.0.0 -::1'
RC config set protected-mode yes
if [[ "$CUR_AUTH" == "none" ]]; then
  RC config set requirepass "$PASS_VAL"
  CUR_AUTH="match"   # 之后所有 RC 调用要带 -a
fi

# AUTH self-test
RC ping | grep -q PONG || err "AUTH self-test 失败"
info "  AUTH OK"

# ============================================================
info "[4/5] 运行时 enable AOF (如果还没开)"
# ============================================================
AOF_NOW=$(RC config get appendonly | tail -1 | tr -d '\r')
if [[ "$AOF_NOW" == "yes" ]]; then
  info "  AOF 已开,跳过"
else
  RC config set appendonly yes
  info "  config set appendonly yes,等 BGREWRITEAOF..."
  while true; do
    R=$(RC info persistence | tr -d '\r' | awk -F: '/^aof_rewrite_in_progress:/{print $2}')
    [[ "$R" == "0" ]] && break
    sleep 1
  done
  ST=$(RC info persistence | tr -d '\r' | awk -F: '/^aof_last_bgrewrite_status:/{print $2}')
  [[ "$ST" == "ok" ]] || err "BGREWRITEAOF 失败 (status=$ST)"
  RC config set appendfsync everysec
  info "  AOF 已开,base 已落盘"
fi

# ============================================================
info "[5/5] config rewrite -> 把运行时配置写回 $REDIS_CONF (重启也是这套)"
# ============================================================
RC config rewrite
sudo grep -nE '^(bind|protected-mode|requirepass|appendonly|appendfsync)' "$REDIS_CONF" | head -10

ss -tlnp 2>/dev/null | grep -E ':6379\s' | head -3 || true

echo
if (( JFS_WAS_ACTIVE )); then
  warn "juicefs 之前在跑 (因首次设密码停掉了),需要重启:"
  warn "  sudo -E bash 04-systemd.sh"
  warn "  sudo systemctl start $JFS_SVC"
else
  info "DONE. 如果是首次部署: sudo -E bash 04-systemd.sh"
fi
