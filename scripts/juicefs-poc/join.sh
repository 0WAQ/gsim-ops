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
#   6. 本机 groups (alpha-core 59000 / alpha-data 59001) + umask 0002 + 本地 sidecar
#   7. ls 挂载点确认看到主节点数据
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
# [0/6] per-host 路径覆盖 -> /etc/juicefs-poc.env
# ============================================================
HOST_ENV="/etc/juicefs-poc.env"
HOST_ENV_EXISTS=0
sudo test -f "$HOST_ENV" && HOST_ENV_EXISTS=1

# client 节点的 META_URL 是必须的 (主节点 META_URL 默认 127.0.0.1, 不需要写)
# 写到 HOST_ENV 让 config.sh / status.sh / 04-systemd 全都从这里读, 单一真值。
NEW_META_URL="redis://${META_HOST}:${META_PORT}/0"

if [[ -n "$ARG_MOUNT$ARG_CACHE$ARG_LOCAL" || $HOST_ENV_EXISTS -eq 0 ]]; then
  info "==> [0/6] 路径覆盖 -> $HOST_ENV"
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
JFS_META_URL=$NEW_META_URL
JFS_REDIS_LOCAL=0
EOF
  sudo chmod 644 "$HOST_ENV"
  info "  JFS_MOUNT=$NEW_MOUNT"
  info "  JFS_CACHE_DIR=$NEW_CACHE"
  info "  JFS_LOCAL_DIR=$NEW_LOCAL"
  info "  JFS_META_URL=$NEW_META_URL"
else
  # 已有 HOST_ENV 但缺 JFS_META_URL (老版本 join.sh 留下的) - 补一行
  if ! sudo grep -q '^JFS_META_URL=' "$HOST_ENV"; then
    sudo sed -i '/^JFS_REDIS_LOCAL=/d' "$HOST_ENV"
    echo "JFS_META_URL=$NEW_META_URL" | sudo tee -a "$HOST_ENV" >/dev/null
    echo 'JFS_REDIS_LOCAL=0' | sudo tee -a "$HOST_ENV" >/dev/null
    info "==> [0/6] 补写 JFS_META_URL=$NEW_META_URL -> $HOST_ENV"
  else
    # 已有 JFS_META_URL 但可能不规范 (老版本可能缺 /0 db 后缀)
    CUR_URL=$(sudo grep -E '^JFS_META_URL=' "$HOST_ENV" | head -1 | cut -d= -f2-)
    if [[ "$CUR_URL" != "$NEW_META_URL" ]]; then
      sudo sed -i "s|^JFS_META_URL=.*|JFS_META_URL=$NEW_META_URL|" "$HOST_ENV"
      info "==> [0/6] normalize JFS_META_URL: $CUR_URL -> $NEW_META_URL"
    fi
  fi
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

info "==> [1/6] juicefs client"
if command -v juicefs >/dev/null; then
  info "  已装: $(juicefs version | head -1)"
else
  JFS_CLIENT_ONLY=1 sudo -E bash ./00-install.sh
fi

info "==> [2/6] 密码 -> $ENV_FILE"
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

info "==> [3/6] 测试 redis AUTH ($META_HOST:$META_PORT)"
sudo bash -c ". $ENV_FILE && redis-cli -h $META_HOST -p $META_PORT -a \"\$META_PASSWORD\" ping" 2>/dev/null \
  | grep -q PONG || err "AUTH 失败,密码不对或网络问题"
info "  PONG"

info "==> [4/6] 渲染 unit + start"
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

info "==> [5/6] 本机 groups + umask + sidecar"
# JFS 里文件 gid 是数字 (59000/59001),本机要有同 gid 的组名才解析得了,
# 组成员才能跑 chmod g+w 之类。client 侧只 inline 这几行,不绕 02-layout。
GID_CORE=59000
GID_DATA=59001
GRP_CORE=alpha-core
GRP_DATA=alpha-data
for pair in "$GID_CORE:$GRP_CORE" "$GID_DATA:$GRP_DATA"; do
  gid=${pair%:*}; name=${pair#*:}
  if getent group "$name" >/dev/null; then
    cur=$(getent group "$name" | cut -d: -f3)
    [[ "$cur" == "$gid" ]] || err "组 $name 已存在但 gid=$cur != $gid"
    info "  $name (gid=$gid) 已存在"
  elif getent group "$gid" >/dev/null; then
    err "gid $gid 已被 '$(getent group "$gid" | cut -d: -f1)' 占用"
  else
    sudo groupadd -g "$gid" "$name"
    info "  + $name (gid=$gid)"
  fi
done
TARGET_USER="${SUDO_USER:-$USER}"
for grp in "$GRP_CORE" "$GRP_DATA"; do
  if id -nG "$TARGET_USER" 2>/dev/null | tr ' ' '\n' | grep -qx "$grp"; then
    info "  $TARGET_USER 已在 $grp"
  else
    sudo usermod -aG "$grp" "$TARGET_USER"
    info "  + $TARGET_USER -> $grp"
  fi
done
UMASK_FILE=/etc/profile.d/ops-umask.sh
if [[ -f "$UMASK_FILE" ]] && grep -q 'umask 0002' "$UMASK_FILE"; then
  info "  $UMASK_FILE 已就绪"
else
  echo 'umask 0002' | sudo tee "$UMASK_FILE" >/dev/null
  sudo chmod 644 "$UMASK_FILE"
  info "  + $UMASK_FILE"
fi
# 本地 sidecar:JFS 里 alpha_dump/staging 是 symlink -> $JFS_LOCAL_DIR/*,
# symlink target 是绝对路径,所以本机这些目录必须存在(且 JFS_LOCAL_DIR 必须和主节点一致)。
sudo mkdir -p "$JFS_LOCAL_DIR"/{alpha_dump,staging}
sudo chown "root:$GRP_DATA" "$JFS_LOCAL_DIR";              sudo chmod 2755 "$JFS_LOCAL_DIR"
sudo chown "root:$GRP_CORE" "$JFS_LOCAL_DIR/staging";      sudo chmod 2750 "$JFS_LOCAL_DIR/staging"
sudo chown "root:$GRP_DATA" "$JFS_LOCAL_DIR/alpha_dump";   sudo chmod 2755 "$JFS_LOCAL_DIR/alpha_dump"
info "  $JFS_LOCAL_DIR/{alpha_dump,staging} 就绪"

info "==> [6/6] 验证挂载点 + sidecar 一致性"
require_mountpoint "$JFS_MOUNT"
info "  $JFS_MOUNT mounted"
ls "$JFS_MOUNT" | sed 's/^/    /'

# JFS 里 alpha_dump/staging 是主节点 02-layout 建的 symlink,
# target 是绝对路径 (主节点的 $JFS_LOCAL_DIR/*)。本机 $JFS_LOCAL_DIR
# 必须和主节点完全一致,否则 symlink dangling = 业务路径全废。
SIDECAR_OK=1
for d in alpha_dump staging; do
  L="$JFS_MOUNT/$d"
  if [[ ! -L "$L" ]]; then
    warn "  $d 不是 symlink (主节点没跑 02-layout,或卷里还没建好)"
    continue
  fi
  expected="$JFS_LOCAL_DIR/$d"
  actual=$(readlink "$L")
  if [[ "$actual" != "$expected" ]]; then
    warn "  $L -> $actual"
    warn "    但本机 JFS_LOCAL_DIR=$JFS_LOCAL_DIR (期望 $expected)"
    warn "    跨节点必须一致:改 $HOST_ENV 的 JFS_LOCAL_DIR 后重跑 join.sh"
    SIDECAR_OK=0
  elif [[ ! -d "$L/" ]]; then
    warn "  $L symlink dangling: 本机缺 $expected"
    SIDECAR_OK=0
  fi
done
(( SIDECAR_OK )) && info "  sidecar symlinks 一致"

echo
info "DONE. 跨节点挂载就绪。"
info "  systemctl status $SVC"
info "  组身份在已开的 SSH session 没生效,验证用 'sg $GRP_CORE -c id' 或重连"
