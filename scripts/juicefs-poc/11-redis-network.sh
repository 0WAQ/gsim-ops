#!/usr/bin/env bash
# 让本机 Redis 监听 0.0.0.0 + requirepass + protected-mode yes,
# 供 JuiceFS 跨节点访问。密码存到 /etc/juicefs/alphalib.env,
# 仅 root 可读,通过 systemd EnvironmentFile 注入 juicefs 进程的 META_PASSWORD,
# 避免出现在 ps / cmdline。
#
# 幂等。再跑一次会复用已有密码,不重新生成。

set -euo pipefail
cd "$(dirname "$0")"
source ./_lib.sh
source ./00-config.sh

require_sudo
require_systemd
require_bin redis-cli   "apt install redis-tools"
require_bin openssl     "apt install openssl"
systemctl list-unit-files | grep -q '^redis-server\.service' \
  || err "找不到 redis-server.service。先跑 01-install.sh"

ENV_FILE="/etc/juicefs/${JFS_NAME}.env"
REDIS_CONF="/etc/redis/redis.conf"
[[ -f "$REDIS_CONF" ]] || err "找不到 $REDIS_CONF。先 apt install redis-server"
MARK_BEGIN="# BEGIN juicefs-poc managed"
MARK_END="# END juicefs-poc managed"

echo "[1/4] 准备密码 -> $ENV_FILE"
sudo install -d -m 0700 -o root -g root /etc/juicefs

if sudo test -f "$ENV_FILE"; then
  echo "  $ENV_FILE 已存在,复用"
else
  PASS="$(openssl rand -hex 24)"
  # 经 stdin,密码不进 argv
  printf 'META_PASSWORD=%s\n' "$PASS" | sudo tee "$ENV_FILE" >/dev/null
  sudo chmod 0600 "$ENV_FILE"
  sudo chown root:root "$ENV_FILE"
  echo "  新建 $ENV_FILE (mode 0600 root:root)"
  unset PASS
fi

# 改 redis 之前必须停掉 juicefs,否则现挂载会遇 AUTH 失败 → 整个挂载点 EIO
JFS_SVC="juicefs-${JFS_NAME}.service"
JFS_WAS_ACTIVE=0
if systemctl is-active --quiet "$JFS_SVC"; then
  JFS_WAS_ACTIVE=1
  echo "[1.5/4] $JFS_SVC 正在跑,先停掉避开 AUTH 中断"
  sudo systemctl stop "$JFS_SVC"
fi

echo "[2/4] 改 $REDIS_CONF (managed block)"
# 先去掉旧 block,再 append 新 block。这样可重跑覆盖。
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
echo "  block 已写入"

echo "[3/4] 重启 redis-server"
sudo systemctl restart redis-server
sleep 1
systemctl is-active --quiet redis-server || { echo "ERROR: redis-server 没起来" >&2; exit 1; }

echo "[4/4] 验证 AUTH"
# 用 sudo 读密码,本地 AUTH,网络监听
sudo bash -c '. /etc/juicefs/alphalib.env && redis-cli -h 127.0.0.1 -a "$META_PASSWORD" ping' 2>/dev/null | grep -q PONG \
  && echo "  127.0.0.1 AUTH ok" \
  || { echo "ERROR: AUTH 失败" >&2; exit 1; }

# 自检监听 IP
ss -tlnp 2>/dev/null | grep -E ':6379\s' | head -3

echo
echo "DONE."
echo
if (( JFS_WAS_ACTIVE )); then
  echo "下一步:re-render systemd unit(让 juicefs 通过 EnvironmentFile 读密码),然后启动:"
  echo "  sudo -E bash scripts/juicefs-poc/10-systemd-unit.sh"
  echo "  sudo systemctl start $JFS_SVC"
else
  echo "下一步:跑 10-systemd-unit.sh 让 unit 带上 EnvironmentFile,然后 start。"
fi
echo
echo "把密码同步到 150 (在 150 上跑):"
echo "  scp 10.9.100.160:/tmp/alphalib.env /tmp/  # 你需要先在 160 上 sudo cp 到 /tmp 给自己"
echo "  或者 ssh 出 redis-cli -h 10.9.100.160 -a <pass> ping 自测密码 ok 即可"
