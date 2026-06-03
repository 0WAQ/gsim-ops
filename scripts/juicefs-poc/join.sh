#!/usr/bin/env bash
# Client 节点(不跑 Redis,只挂 JuiceFS)一键加入集群:
#
#   1. 自检 sudo / 网络 / 缺失二进制
#   2. 装 juicefs client(如果还没装)
#   3. 拿密码(三种方式,优先级递减):
#        a) --password-stdin   从 stdin 读
#        b) $META_PASSWORD     从环境读
#        c) 交互提示输入(隐藏回显)
#      写到 /etc/juicefs/<name>.env (0600 root:root)
#   4. 测试到 meta-host 的 redis AUTH
#   5. 渲染 + 启动 juicefs-<name>.service(JFS_REDIS_LOCAL=0)
#   6. ls 挂载点确认看到主节点数据
#
# 用法:
#   sudo -E bash join.sh --meta-host 10.9.100.160
#   echo "$PASS" | sudo -E bash join.sh --meta-host 10.9.100.160 --password-stdin
#   META_PASSWORD=xxx sudo -E bash join.sh --meta-host 10.9.100.160
#
# 幂等。

set -euo pipefail
cd "$(dirname "$0")"
source ./_lib.sh
source ./config.sh

META_HOST=""
META_PORT="6379"
PASSWORD_STDIN=0

while (( $# )); do
  case "$1" in
    --meta-host)      META_HOST=$2; shift 2;;
    --meta-port)      META_PORT=$2; shift 2;;
    --password-stdin) PASSWORD_STDIN=1; shift;;
    -h|--help) sed -n '2,/^$/p' "$0"; exit 0;;
    *) err "未知参数: $1";;
  esac
done

[[ -n "$META_HOST" ]] || err "缺 --meta-host <ip>"

ENV_FILE="/etc/juicefs/${JFS_NAME}.env"
SVC="juicefs-${JFS_NAME}.service"
META_URL="redis://${META_HOST}:${META_PORT}/0"

info "==> 自检"
require_sudo
require_systemd
require_bin curl
require_bin redis-cli "apt install redis-tools"
require_tcp "$META_HOST" "$META_PORT" 5

info "==> [1/5] juicefs client"
if command -v juicefs >/dev/null; then
  info "  已装: $(juicefs version | head -1)"
else
  JFS_CLIENT_ONLY=1 sudo -E bash ./00-install.sh
fi

info "==> [2/5] 密码 -> $ENV_FILE"
sudo install -d -m 0700 -o root -g root /etc/juicefs

if sudo test -f "$ENV_FILE"; then
  info "  已存在,复用"
else
  PASS=""
  if (( PASSWORD_STDIN )); then
    IFS= read -r PASS
  elif [[ -n "${META_PASSWORD:-}" ]]; then
    PASS="$META_PASSWORD"
  else
    read -r -s -p "  粘贴 META_PASSWORD (在主节点 sudo cat $ENV_FILE 取): " PASS
    echo
  fi
  [[ -n "$PASS" ]] || err "密码为空"
  printf 'META_PASSWORD=%s\n' "$PASS" | sudo tee "$ENV_FILE" >/dev/null
  sudo chmod 0600 "$ENV_FILE"
  sudo chown root:root "$ENV_FILE"
  unset PASS
  info "  写入完成 (0600 root:root)"
fi

info "==> [3/5] 测试 redis AUTH ($META_HOST:$META_PORT)"
sudo bash -c ". $ENV_FILE && redis-cli -h $META_HOST -p $META_PORT -a \"\$META_PASSWORD\" ping" 2>/dev/null \
  | grep -q PONG || err "AUTH 失败,密码不对或网络问题"
info "  PONG"

info "==> [4/5] 渲染 unit + start"
JFS_META_URL="$META_URL" JFS_REDIS_LOCAL=0 sudo -E bash ./04-systemd.sh >/dev/null
if systemctl is-active --quiet "$SVC"; then
  info "  $SVC 已 running"
else
  sudo systemctl start "$SVC"
  sleep 1
  systemctl is-active --quiet "$SVC" || err "$SVC 启动失败,看 journalctl -u $SVC"
  info "  $SVC started"
fi

info "==> [5/5] 验证挂载点"
require_mountpoint "$JFS_MOUNT"
info "  $JFS_MOUNT mounted"
ls "$JFS_MOUNT" | sed 's/^/    /'

echo
info "DONE. 跨节点挂载就绪。"
info "  systemctl status $SVC"
