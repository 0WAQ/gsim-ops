#!/usr/bin/env bash
# 主节点一键 bootstrap。每个 stage 幂等,可单独跑也可全跑。
#
# 用法:
#   sudo -E bash bootstrap.sh                # 一键全跑(install -> systemd)
#   sudo -E bash bootstrap.sh install        # 只装 redis + juicefs
#   sudo -E bash bootstrap.sh provision      # MinIO bucket + format JFS + 临时挂载
#   sudo -E bash bootstrap.sh layout         # sidecar symlink + 两组权限
#   sudo -E bash bootstrap.sh redis          # redis 网络化 + 密码 + AUTH
#   sudo -E bash bootstrap.sh systemd        # 渲染 juicefs-<name>.service
#
# Client 节点用 join.sh,不要跑这个。
#
# 环境变量:
#   JFS_CLIENT_ONLY=1   install 阶段跳过 redis(给 join.sh 用)
#   JFS_REDIS_LOCAL=0   systemd 阶段不依赖本地 redis-server.service(给 join.sh 用)

set -euo pipefail
cd "$(dirname "$0")"
source ./_lib.sh
source ./config.sh

STAGES_ALL=(install provision layout redis systemd)

# ============================================================
# stage: install
# ============================================================
stage_install() {
  info "== install: redis + juicefs =="
  require_sudo

  local PM
  if   command -v apt-get >/dev/null; then PM=apt
  elif command -v dnf >/dev/null;     then PM=dnf
  elif command -v yum >/dev/null;     then PM=yum
  else err "不认识的包管理器,手动装 redis"
  fi

  if [[ "${JFS_CLIENT_ONLY:-0}" == "1" ]]; then
    info "  [skip] JFS_CLIENT_ONLY=1, 不装 redis"
  else
    info "  [1/2] 装 redis ($PM)"
    case $PM in
      apt) sudo apt-get update -qq && sudo apt-get install -y redis-server ;;
      dnf|yum) sudo $PM install -y redis ;;
    esac
    sudo systemctl enable --now redis-server 2>/dev/null \
      || sudo systemctl enable --now redis 2>/dev/null \
      || warn "  无法 enable 启动,看下系统是否用别名"
    if redis-cli ping 2>/dev/null | grep -q PONG; then
      info "  redis PONG"
    else
      warn "  redis-cli ping 无 PONG(可能已配 requirepass,后续 stage redis 会再测)"
    fi
  fi

  info "  [2/2] 装 juicefs client"
  if command -v juicefs >/dev/null; then
    info "    已装: $(juicefs version | head -1)"
  else
    curl -sSL https://d.juicefs.com/install | sudo sh -
    juicefs version | head -1
  fi
}

# ============================================================
# stage: provision (MinIO bucket + format + 临时挂载)
# ============================================================
stage_provision() {
  info "== provision: bucket + format + mount =="
  require_sudo
  require_bin juicefs "curl -sSL https://d.juicefs.com/install | sudo sh -"
  require_bin rclone "apt install rclone"

  [[ -n "$MINIO_ENDPOINT" && -n "$MINIO_ACCESS_KEY" && -n "$MINIO_SECRET_KEY" ]] \
    || err "provision 阶段需要 MinIO 凭证(MINIO_ROOT_USER/MINIO_ROOT_PASSWORD 或 rclone.conf)"

  # 临时 rclone 配置,避开默认 conf 里权限受限的 profile
  local TMP_CONF
  TMP_CONF="$(mktemp -t juicefs-poc-rclone-XXXXXX.conf)"
  trap 'rm -f "$TMP_CONF"' RETURN
  cat > "$TMP_CONF" <<EOF
[poc]
type = s3
provider = Minio
endpoint = $MINIO_ENDPOINT
access_key_id = $MINIO_ACCESS_KEY
secret_access_key = $MINIO_SECRET_KEY
EOF
  chmod 600 "$TMP_CONF"
  local RCLONE="rclone --config $TMP_CONF"

  info "  [1/4] bucket '$JFS_BUCKET'"
  if $RCLONE lsd "poc:${JFS_BUCKET}" >/dev/null 2>&1; then
    info "    已存在,跳过"
  else
    $RCLONE mkdir "poc:${JFS_BUCKET}"
    info "    mkdir issued"
  fi
  # rclone mkdir 在 no_check_bucket=true 时会假装成功,这里走真 PutObject 兜底
  local PROBE_KEY="_poc_probe_$$.txt"
  echo "hello juicefs poc" | $RCLONE rcat "poc:${JFS_BUCKET}/${PROBE_KEY}"
  $RCLONE ls "poc:${JFS_BUCKET}/${PROBE_KEY}" >/dev/null
  $RCLONE delete "poc:${JFS_BUCKET}/${PROBE_KEY}"
  info "    write/list/delete ok"

  info "  [2/4] cache dir '$JFS_CACHE_DIR'"
  if [[ -d "$JFS_CACHE_DIR" ]]; then
    info "    已存在,跳过"
  else
    sudo mkdir -p "$JFS_CACHE_DIR"
    sudo chown "${SUDO_USER:-$USER}:$(id -gn "${SUDO_USER:-$USER}")" "$JFS_CACHE_DIR"
    info "    created"
  fi

  info "  [3/4] juicefs format (幂等)"
  juicefs format \
    --storage minio \
    --bucket "${MINIO_ENDPOINT}/${JFS_BUCKET}" \
    --access-key "$MINIO_ACCESS_KEY" \
    --secret-key "$MINIO_SECRET_KEY" \
    "$JFS_META_URL" \
    "$JFS_NAME"

  info "  [4/4] 临时挂载到 $JFS_MOUNT"
  if [[ ! -d "$JFS_MOUNT" ]]; then
    sudo mkdir -p "$JFS_MOUNT"
    sudo chown "${SUDO_USER:-$USER}:$(id -gn "${SUDO_USER:-$USER}")" "$JFS_MOUNT"
  fi
  if mountpoint -q "$JFS_MOUNT"; then
    info "    已挂载"
  else
    juicefs mount \
      --cache-dir "$JFS_CACHE_DIR" \
      --cache-size "$JFS_CACHE_SIZE_MB" \
      --writeback --background \
      "$JFS_META_URL" "$JFS_MOUNT"
    sleep 1
    info "    mounted"
  fi
}

# ============================================================
# stage: layout (sidecar symlink + 两组权限)
# ============================================================
stage_layout() {
  info "== layout: sidecar symlink + 两组权限 =="
  require_sudo
  require_mountpoint "$JFS_MOUNT"

  local LOCAL_DIRS=(alpha_dump staging recycle)
  local REAL_USER="${SUDO_USER:-$USER}"
  local REAL_GROUP; REAL_GROUP="$(id -gn "$REAL_USER")"

  info "  [1/3] 本地后备目录 $JFS_LOCAL_DIR"
  sudo mkdir -p "$JFS_LOCAL_DIR"
  for d in "${LOCAL_DIRS[@]}"; do sudo mkdir -p "$JFS_LOCAL_DIR/$d"; done
  sudo chown -R "$REAL_USER:$REAL_GROUP" "$JFS_LOCAL_DIR"
  sudo chmod -R u=rwX,g=rwX,o=rX "$JFS_LOCAL_DIR"

  info "  [2/3] 挂载点里 ${LOCAL_DIRS[*]} 改 symlink"
  for d in "${LOCAL_DIRS[@]}"; do
    local target="$JFS_MOUNT/$d" expected="$JFS_LOCAL_DIR/$d"
    if [[ -L "$target" ]]; then
      local cur; cur="$(readlink "$target")"
      if [[ "$cur" == "$expected" ]]; then info "    $d  symlink ok"; continue; fi
      info "    $d  symlink 指向 $cur, 修正"
      rm "$target"
    elif [[ -d "$target" ]]; then
      if find "$target" -type f -print -quit | grep -q .; then
        err "$target 含文件,拒绝替换为 symlink(人工确认数据)"
      fi
      rm -rf "$target"
    fi
    ln -s "$expected" "$target"
    info "    $d -> $expected"
  done

  # 顺手清掉 PoC 早期对照仓库
  local POC_REMNANT="$(dirname "$JFS_MOUNT")/_poc_git_local"
  [[ -d "$POC_REMNANT" ]] && sudo rm -rf "$POC_REMNANT" && info "    cleaned $POC_REMNANT"

  info "  [3/3] 应用两组权限模型"
  _apply_perms
}

_apply_perms() {
  local GID_CORE=59000 GID_DATA=59001
  local GRP_CORE="alpha-core" GRP_DATA="alpha-data"
  local CORE_MEMBERS=(wbai) DATA_MEMBERS=(wbai)

  _ensure_group "$GID_CORE" "$GRP_CORE"
  _ensure_group "$GID_DATA" "$GRP_DATA"
  for u in "${CORE_MEMBERS[@]}"; do _ensure_member "$u" "$GRP_CORE"; done
  for u in "${DATA_MEMBERS[@]}"; do _ensure_member "$u" "$GRP_DATA"; done

  # 清掉 alpha-author-* 残留
  mapfile -t stale < <(getent group | awk -F: '$1 ~ /^alpha-author-/ {print $1}')
  for g in "${stale[@]:-}"; do
    [[ -z "$g" ]] && continue
    local members; members=$(getent group "$g" | awk -F: '{print $4}')
    IFS=',' read -ra arr <<< "$members"
    for m in "${arr[@]}"; do [[ -n "$m" ]] && sudo gpasswd -d "$m" "$g" >/dev/null; done
    sudo groupdel "$g"
    info "    cleaned $g"
  done

  # JFS 顶层
  sudo chown "root:$GRP_DATA" "$JFS_MOUNT"; sudo chmod 2755 "$JFS_MOUNT"
  info "    $JFS_MOUNT  (root:$GRP_DATA 2755)"
  _apply_dir "$JFS_MOUNT/alpha_src"     "root:$GRP_CORE" "u=rwX,g=rX,o="
  _apply_dir "$JFS_MOUNT/alpha_pnl"     "root:$GRP_DATA" "u=rwX,g=rwX,o=rX"
  _apply_dir "$JFS_MOUNT/alpha_feature" "root:$GRP_DATA" "u=rwX,g=rwX,o=rX"

  # 本地 sidecar
  [[ -d "$JFS_LOCAL_DIR" ]] || err "$JFS_LOCAL_DIR 不存在"
  sudo chown "root:$GRP_DATA" "$JFS_LOCAL_DIR"; sudo chmod 2755 "$JFS_LOCAL_DIR"
  info "    $JFS_LOCAL_DIR  (root:$GRP_DATA 2755)"
  _apply_dir "$JFS_LOCAL_DIR/staging"    "root:$GRP_CORE" "u=rwX,g=rwX,o="
  _apply_dir "$JFS_LOCAL_DIR/alpha_dump" "root:$GRP_DATA" "u=rwX,g=rwX,o=rX"

  # recycle: sticky 顶层, 子目录(嵌套一层 unixId)按用户单独 chown
  local RECYCLE="$JFS_LOCAL_DIR/recycle"
  if [[ -d "$RECYCLE" ]]; then
    sudo chown root:root "$RECYCLE"
    sudo chmod 1755 "$RECYCLE"
    sudo chmod g-s "$RECYCLE"   # 清掉早期 setgid 残留
    info "    $RECYCLE  (root:root 1755 sticky)"
    for sub in "$RECYCLE"/*/; do
      [[ -d "$sub" ]] || continue
      local uid; uid=$(basename "$sub")
      if ! getent passwd "$uid" >/dev/null; then warn "    recycle/$uid 无系统用户"; continue; fi
      local pgrp; pgrp=$(id -gn "$uid")
      sudo chown -R "$uid:$pgrp" "$sub"
      sudo chmod -R u=rwX,g=,o= "$sub"
      sudo find "$sub" -type d -exec chmod g-s {} +
      info "    $sub  ($uid:$pgrp 0700)"
    done
  fi

  warn "  研究员 shell 必须 umask 0002:"
  warn "    echo 'umask 0002' | sudo tee /etc/profile.d/ops-umask.sh && sudo chmod 644 /etc/profile.d/ops-umask.sh"
}

_ensure_group() {
  local gid=$1 name=$2 entry owner_by_gid="" cur_gid
  if entry=$(getent group "$gid" 2>/dev/null); then
    owner_by_gid=$(printf '%s\n' "$entry" | cut -d: -f1)
  fi
  if [[ -n "$owner_by_gid" && "$owner_by_gid" != "$name" ]]; then
    err "gid $gid 已被 '$owner_by_gid' 占用"
  fi
  if entry=$(getent group "$name" 2>/dev/null); then
    cur_gid=$(printf '%s\n' "$entry" | cut -d: -f3)
    [[ "$cur_gid" == "$gid" ]] || err "组 $name 已存在但 gid=$cur_gid != $gid"
  else
    sudo groupadd -g "$gid" "$name"
    info "    + $name (gid=$gid)"
  fi
}

_ensure_member() {
  local user=$1 grp=$2
  if id -nG "$user" 2>/dev/null | tr ' ' '\n' | grep -qx "$grp"; then return; fi
  sudo usermod -aG "$grp" "$user"
  info "    + $user -> $grp"
}

_apply_dir() {
  local d=$1 owner=$2 mode=$3
  [[ -d "$d" ]] || { info "    skip $d (不存在)"; return; }
  sudo setfacl -R -b "$d" 2>/dev/null || true
  sudo setfacl -R -k "$d" 2>/dev/null || true
  sudo chown -R "$owner" "$d"
  sudo chmod -R "$mode" "$d"
  sudo find "$d" -type d -exec chmod g+s {} +
  info "    $d  ($owner $mode + setgid)"
}

# ============================================================
# stage: redis (网络化 + 密码 + AUTH)
# ============================================================
stage_redis() {
  info "== redis: 网络化 + requirepass =="
  require_sudo
  require_systemd
  require_bin redis-cli "apt install redis-tools"
  require_bin openssl   "apt install openssl"
  systemctl list-unit-files | grep -q '^redis-server\.service' \
    || err "找不到 redis-server.service。先跑 install"

  local ENV_FILE="/etc/juicefs/${JFS_NAME}.env"
  local REDIS_CONF="/etc/redis/redis.conf"
  [[ -f "$REDIS_CONF" ]] || err "找不到 $REDIS_CONF"
  local MARK_BEGIN="# BEGIN juicefs-poc managed"
  local MARK_END="# END juicefs-poc managed"

  info "  [1/4] 密码 -> $ENV_FILE"
  sudo install -d -m 0700 -o root -g root /etc/juicefs
  if sudo test -f "$ENV_FILE"; then
    info "    已存在,复用"
  else
    local PASS; PASS="$(openssl rand -hex 24)"
    printf 'META_PASSWORD=%s\n' "$PASS" | sudo tee "$ENV_FILE" >/dev/null
    sudo chmod 0600 "$ENV_FILE"
    sudo chown root:root "$ENV_FILE"
    unset PASS
    info "    新建 (0600 root:root)"
  fi

  # 改 redis 前必须停 juicefs,否则现挂载在 AUTH 切换瞬间全 EIO
  local JFS_SVC="juicefs-${JFS_NAME}.service" JFS_WAS_ACTIVE=0
  if systemctl is-active --quiet "$JFS_SVC"; then
    JFS_WAS_ACTIVE=1
    info "    $JFS_SVC 在跑,先停掉避开 AUTH 中断"
    sudo systemctl stop "$JFS_SVC"
  fi

  info "  [2/4] 改 $REDIS_CONF"
  sudo sed -i.bak "/^${MARK_BEGIN}/,/^${MARK_END}/d" "$REDIS_CONF"
  local PASS_VAL; PASS_VAL="$(sudo grep -E '^META_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"
  sudo tee -a "$REDIS_CONF" >/dev/null <<EOF
$MARK_BEGIN
bind 0.0.0.0 -::1
protected-mode yes
requirepass $PASS_VAL
$MARK_END
EOF
  unset PASS_VAL

  info "  [3/4] restart redis-server"
  sudo systemctl restart redis-server
  sleep 1
  systemctl is-active --quiet redis-server || err "redis-server 没起来"

  info "  [4/4] AUTH self-test"
  sudo bash -c ". $ENV_FILE && redis-cli -h 127.0.0.1 -a \"\$META_PASSWORD\" ping" 2>/dev/null \
    | grep -q PONG || err "AUTH 失败"
  info "    127.0.0.1 PONG"
  ss -tlnp 2>/dev/null | grep -E ':6379\s' | head -3 || true

  if (( JFS_WAS_ACTIVE )); then
    warn "  juicefs 之前在跑,需要重新渲染 unit + start:"
    warn "    sudo -E bash bootstrap.sh systemd"
    warn "    sudo systemctl start $JFS_SVC"
  fi
}

# ============================================================
# stage: systemd (渲染 juicefs-<name>.service)
# ============================================================
stage_systemd() {
  info "== systemd: juicefs-${JFS_NAME}.service =="
  require_sudo
  require_systemd
  require_bin juicefs "curl -sSL https://d.juicefs.com/install | sudo sh -"

  local JUICEFS_BIN UNIT_NAME UNIT_PATH ENV_FILE
  JUICEFS_BIN="$(command -v juicefs)"
  UNIT_NAME="juicefs-${JFS_NAME}.service"
  UNIT_PATH="/etc/systemd/system/$UNIT_NAME"
  ENV_FILE="/etc/juicefs/${JFS_NAME}.env"

  # 跨节点:JFS_REDIS_LOCAL=0 让 client 不依赖本地 redis-server
  local JFS_REDIS_LOCAL="${JFS_REDIS_LOCAL:-1}"
  local REQ_LINE="" AFTER_LINE="After=network-online.target"
  if [[ "$JFS_REDIS_LOCAL" == "1" ]]; then
    REQ_LINE="Requires=redis-server.service"
    AFTER_LINE="After=network-online.target redis-server.service"
  fi

  local ENV_LINE=""
  if sudo test -f "$ENV_FILE"; then
    ENV_LINE="EnvironmentFile=$ENV_FILE"
    info "  检测到 $ENV_FILE, 通过 EnvironmentFile 注入 META_PASSWORD"
  fi

  info "  [1/3] 渲染 -> $UNIT_PATH"
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
ExecStop=$JUICEFS_BIN umount $JFS_MOUNT
Restart=on-failure
RestartSec=5
TimeoutStartSec=60
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
EOF
  sudo chmod 644 "$UNIT_PATH"

  info "  [2/3] daemon-reload + enable"
  sudo systemctl daemon-reload
  sudo systemctl enable "$UNIT_NAME" >/dev/null

  info "  [3/3] 当前状态"
  if mountpoint -q "$JFS_MOUNT"; then
    info "    $JFS_MOUNT 已挂载(手动挂的)。systemd 接管需:"
    info "      sudo $JUICEFS_BIN umount $JFS_MOUNT"
    info "      sudo systemctl start $UNIT_NAME"
  else
    info "    $JFS_MOUNT 未挂载。启动:"
    info "      sudo systemctl start $UNIT_NAME"
  fi
}

# ============================================================
# main dispatcher
# ============================================================
usage() {
  echo "用法: sudo -E bash bootstrap.sh [${STAGES_ALL[*]}]"
  echo "无参数时按顺序全跑。"
  exit 1
}

if [[ $# -eq 0 ]]; then
  STAGES=("${STAGES_ALL[@]}")
elif [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
else
  STAGES=("$@")
  for s in "${STAGES[@]}"; do
    if ! printf '%s\n' "${STAGES_ALL[@]}" | grep -qx "$s"; then
      err "未知 stage: $s (可选: ${STAGES_ALL[*]})"
    fi
  done
fi

for s in "${STAGES[@]}"; do
  "stage_$s"
  echo
done

info "DONE."
