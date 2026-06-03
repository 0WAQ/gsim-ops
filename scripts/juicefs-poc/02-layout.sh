#!/usr/bin/env bash
# 把 alpha_dump / staging / recycle 改为指向本地 sidecar 的 symlink,
# 然后应用 alpha-core / alpha-data 两组权限模型。
# 不用 POSIX ACL,靠 setgid + umask 0002。

set -euo pipefail
cd "$(dirname "$0")"
source ./_lib.sh
source ./config.sh

require_sudo
require_mountpoint "$JFS_MOUNT"

LOCAL_DIRS=(alpha_dump staging recycle)
REAL_USER="${SUDO_USER:-$USER}"
REAL_GROUP="$(id -gn "$REAL_USER")"

GID_CORE=59000
GID_DATA=59001
GRP_CORE="alpha-core"
GRP_DATA="alpha-data"
CORE_MEMBERS=(wbai)
DATA_MEMBERS=(wbai)

ensure_group() {
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
    info "  + $name (gid=$gid)"
  fi
}

ensure_member() {
  local user=$1 grp=$2
  if id -nG "$user" 2>/dev/null | tr ' ' '\n' | grep -qx "$grp"; then return; fi
  sudo usermod -aG "$grp" "$user"
  info "  + $user -> $grp"
}

apply_dir() {
  local d=$1 owner=$2 mode=$3
  [[ -d "$d" ]] || { info "  skip $d (不存在)"; return; }
  sudo setfacl -R -b "$d" 2>/dev/null || true
  sudo setfacl -R -k "$d" 2>/dev/null || true
  sudo chown -R "$owner" "$d"
  sudo chmod -R "$mode" "$d"
  sudo find "$d" -type d -exec chmod g+s {} +
  info "  $d  ($owner $mode + setgid)"
}

info "[1/4] 本地 sidecar $JFS_LOCAL_DIR"
sudo mkdir -p "$JFS_LOCAL_DIR"
for d in "${LOCAL_DIRS[@]}"; do sudo mkdir -p "$JFS_LOCAL_DIR/$d"; done
sudo chown -R "$REAL_USER:$REAL_GROUP" "$JFS_LOCAL_DIR"
sudo chmod -R u=rwX,g=rwX,o=rX "$JFS_LOCAL_DIR"

info "[2/4] 挂载点里 ${LOCAL_DIRS[*]} 改 symlink"
for d in "${LOCAL_DIRS[@]}"; do
  target="$JFS_MOUNT/$d"
  expected="$JFS_LOCAL_DIR/$d"
  if [[ -L "$target" ]]; then
    cur="$(readlink "$target")"
    if [[ "$cur" == "$expected" ]]; then info "  $d  symlink ok"; continue; fi
    info "  $d  symlink 指向 $cur, 修正"
    rm "$target"
  elif [[ -d "$target" ]]; then
    if find "$target" -type f -print -quit | grep -q .; then
      err "$target 含文件,拒绝替换 symlink(人工确认数据)"
    fi
    rm -rf "$target"
  fi
  ln -s "$expected" "$target"
  info "  $d -> $expected"
done

# 顺手清掉 PoC 早期对照仓库
POC_REMNANT="$(dirname "$JFS_MOUNT")/_poc_git_local"
[[ -d "$POC_REMNANT" ]] && sudo rm -rf "$POC_REMNANT" && info "  cleaned $POC_REMNANT"

info "[3/4] 组 + 成员"
ensure_group "$GID_CORE" "$GRP_CORE"
ensure_group "$GID_DATA" "$GRP_DATA"
for u in "${CORE_MEMBERS[@]}"; do ensure_member "$u" "$GRP_CORE"; done
for u in "${DATA_MEMBERS[@]}"; do ensure_member "$u" "$GRP_DATA"; done

# 清掉 alpha-author-* 残留
mapfile -t stale < <(getent group | awk -F: '$1 ~ /^alpha-author-/ {print $1}')
for g in "${stale[@]:-}"; do
  [[ -z "$g" ]] && continue
  members=$(getent group "$g" | awk -F: '{print $4}')
  IFS=',' read -ra arr <<< "$members"
  for m in "${arr[@]}"; do [[ -n "$m" ]] && sudo gpasswd -d "$m" "$g" >/dev/null; done
  sudo groupdel "$g"
  info "  cleaned $g"
done

info "[4/4] 应用权限"
# JFS 顶层
sudo chown "root:$GRP_DATA" "$JFS_MOUNT"; sudo chmod 2755 "$JFS_MOUNT"
info "  $JFS_MOUNT  (root:$GRP_DATA 2755)"
apply_dir "$JFS_MOUNT/alpha_src"     "root:$GRP_CORE" "u=rwX,g=rX,o="
apply_dir "$JFS_MOUNT/alpha_pnl"     "root:$GRP_DATA" "u=rwX,g=rwX,o=rX"
apply_dir "$JFS_MOUNT/alpha_feature" "root:$GRP_DATA" "u=rwX,g=rwX,o=rX"

# 本地 sidecar 顶层
sudo chown "root:$GRP_DATA" "$JFS_LOCAL_DIR"; sudo chmod 2755 "$JFS_LOCAL_DIR"
info "  $JFS_LOCAL_DIR  (root:$GRP_DATA 2755)"
apply_dir "$JFS_LOCAL_DIR/staging"    "root:$GRP_CORE" "u=rwX,g=rwX,o="
apply_dir "$JFS_LOCAL_DIR/alpha_dump" "root:$GRP_DATA" "u=rwX,g=rwX,o=rX"

# recycle: sticky 顶层, 子目录(嵌套一层 unixId)按用户单独 chown
RECYCLE="$JFS_LOCAL_DIR/recycle"
if [[ -d "$RECYCLE" ]]; then
  sudo chown root:root "$RECYCLE"
  sudo chmod 1755 "$RECYCLE"
  sudo chmod g-s "$RECYCLE"   # 清掉早期 setgid 残留
  info "  $RECYCLE  (root:root 1755 sticky)"
  for sub in "$RECYCLE"/*/; do
    [[ -d "$sub" ]] || continue
    uid=$(basename "$sub")
    if ! getent passwd "$uid" >/dev/null; then warn "  recycle/$uid 无系统用户"; continue; fi
    pgrp=$(id -gn "$uid")
    sudo chown -R "$uid:$pgrp" "$sub"
    sudo chmod -R u=rwX,g=,o= "$sub"
    sudo find "$sub" -type d -exec chmod g-s {} +
    info "  $sub  ($uid:$pgrp 0700)"
  done
fi

echo
warn "研究员 shell 必须 umask 0002:"
warn "  echo 'umask 0002' | sudo tee /etc/profile.d/ops-umask.sh && sudo chmod 644 /etc/profile.d/ops-umask.sh"
echo
info "DONE. 下一步: sudo -E bash 03-redis.sh"
