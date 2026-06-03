#!/usr/bin/env bash
# Client 节点(不跑 Redis,只挂 JuiceFS)一键加入集群:
#
#   0. 把 --mount / --cache / --local 写到 /etc/juicefs-poc.env(per-host 路径覆盖)
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
#   sudo -E bash join.sh \
#     --meta-host 10.9.100.160 \
#     --mount  /mnt/jfs/alphalib \
#     --cache  /mnt/jfs/cache \
#     --local  /mnt/jfs/alphalib.local
#
#   # 路径已经在 /etc/juicefs-poc.env 写过,可省略 --mount/--cache/--local
#   sudo -E bash join.sh --meta-host 10.9.100.160
#
#   # 非交互输密码
#   echo "$PASS" | sudo -E bash join.sh --meta-host 10.9.100.160 --password-stdin
#   META_PASSWORD=xxx sudo -E bash join.sh --meta-host 10.9.100.160
#
# 幂等。

set -euo pipefail
cd "$(dirname "$0")"
source ./_lib.sh

META_HOST=""
META_PORT="6379"
PASSWORD_STDIN=0
ARG_MOUNT=""
ARG_CACHE=""
ARG_LOCAL=""

while (( $# )); do
  case "$1" in
    --meta-host)      META_HOST=$2; shift 2;;
    --meta-port)      META_PORT=$2; shift 2;;
    --mount)          ARG_MOUNT=$2; shift 2;;
    --cache)          ARG_CACHE=$2; shift 2;;
    --local)          ARG_LOCAL=$2; shift 2;;
    --password-stdin) PASSWORD_STDIN=1; shift;;
    -h|--help) sed -n '2,/^$/p' "$0"; exit 0;;
    *) err "未知参数: $1";;
  esac
done

[[ -n "$META_HOST" ]] || err "缺 --meta-host <ip>"

require_sudo

# ============================================================
# [0/5] per-host 路径覆盖 -> /etc/juicefs-poc.env
# ============================================================
HOST_ENV="/etc/juicefs-poc.env"
HOST_ENV_EXISTS=0
sudo test -f "$HOST_ENV" && HOST_ENV_EXISTS=1

if [[ -n "$ARG_MOUNT$ARG_CACHE$ARG_LOCAL" || $HOST_ENV_EXISTS -eq 0 ]]; then
  info "==> [0/5] 路径覆盖 -> $HOST_ENV"
  # 读旧值(如果有),命令行参数覆盖之
  OLD_MOUNT="" OLD_CACHE="" OLD_LOCAL=""
  if (( HOST_ENV_EXISTS )); then
    OLD_MOUNT=$(sudo grep -E '^JFS_MOUNT='     "$HOST_ENV" | cut -d= -f2- || true)
    OLD_CACHE=$(sudo grep -E '^JFS_CACHE_DIR=' "$HOST_ENV" | cut -d= -f2- || true)
    OLD_LOCAL=$(sudo grep -E '^JFS_LOCAL_DIR=' "$HOST_ENV" | cut -d= -f2- || true)
  fi
  NEW_MOUNT="${ARG_MOUNT:-$OLD_MOUNT}"
  NEW_CACHE="${ARG_CACHE:-$OLD_CACHE}"
  NEW_LOCAL="${ARG_LOCAL:-$OLD_LOCAL}"
  # LOCAL 不强求,缺省就 MOUNT.local
  [[ -z "$NEW_LOCAL" && -n "$NEW_MOUNT" ]] && NEW_LOCAL="${NEW_MOUNT}.local"

  [[ -n "$NEW_MOUNT" ]] || err "首次部署必须给 --mount <path>"
  [[ -n "$NEW_CACHE" ]] || err "首次部署必须给 --cache <path>"

  sudo tee "$HOST_ENV" >/dev/null <<EOF
# Per-host JuiceFS path override. Written by join.sh.
JFS_MOUNT=$NEW_MOUNT
JFS_CACHE_DIR=$NEW_CACHE
JFS_LOCAL_DIR=$NEW_LOCAL
EOF
  sudo chmod 644 "$HOST_ENV"
  info "  JFS_MOUNT=$NEW_MOUNT"
  info "  JFS_CACHE_DIR=$NEW_CACHE"
  info "  JFS_LOCAL_DIR=$NEW_LOCAL"
fi

# 现在才 source config.sh(会自动 pick up /etc/juicefs-poc.env)
source ./config.sh

ENV_FILE="/etc/juicefs/${JFS_NAME}.env"
SVC="juicefs-${JFS_NAME}.service"
META_URL="redis://${META_HOST}:${META_PORT}/0"

info "==> 自检"
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
# 注意:JFS_META_URL 在这里通过 env 传给 04-systemd.sh,
# 04-systemd.sh 会 source 它自己的 config.sh(也会 pick up /etc/juicefs-poc.env)
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
