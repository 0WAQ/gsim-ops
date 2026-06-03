#!/usr/bin/env bash
# 两层组权限模型。owner 一律 root(recycle 子目录除外),enforcement 走 gid。
# 不用 POSIX ACL,只靠 setgid + umask 0002(umask 由 /etc/profile.d/ops-umask.sh 统一设)。
#
# 组:
#   alpha-core (59000)  核心组,读 alpha_src 和 staging
#   alpha-data (59001)  数据组,读写 alpha_pnl/feature/dump
#
# 布局:
#   JFS  alpha_src       root:alpha-core 2750   core 读
#        alpha_pnl       root:alpha-data 2775   data 读写,others 读
#        alpha_feature   root:alpha-data 2775   data 读写,others 读
#   本地 staging         root:alpha-core 2770   core 读写
#        alpha_dump      root:alpha-data 2775   data 读写,others 读
#        recycle         root:root       1755   sticky,可穿越
#        recycle/<uid>   <uid>:<primary> 0700   只用户自己
#
# 幂等,可重复跑。

set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh

GID_CORE=59000
GID_DATA=59001
GRP_CORE="alpha-core"
GRP_DATA="alpha-data"

CORE_MEMBERS=(wbai)
DATA_MEMBERS=(wbai)

SRC="$JFS_MOUNT/alpha_src"
PNL="$JFS_MOUNT/alpha_pnl"
FEAT="$JFS_MOUNT/alpha_feature"

LOCAL="$JFS_LOCAL_DIR"
STAGING="$LOCAL/staging"
DUMP="$LOCAL/alpha_dump"
RECYCLE="$LOCAL/recycle"

if ! mountpoint -q "$JFS_MOUNT"; then
  echo "ERROR: $JFS_MOUNT 未挂载" >&2; exit 1
fi

ensure_group() {
  local gid=$1 name=$2 entry owner_by_gid="" cur_gid
  if entry=$(getent group "$gid" 2>/dev/null); then
    owner_by_gid=$(printf '%s\n' "$entry" | cut -d: -f1)
  fi
  if [[ -n "$owner_by_gid" && "$owner_by_gid" != "$name" ]]; then
    echo "  ERROR: gid $gid 已被 '$owner_by_gid' 占用" >&2; exit 1
  fi
  if entry=$(getent group "$name" 2>/dev/null); then
    cur_gid=$(printf '%s\n' "$entry" | cut -d: -f3)
    if [[ "$cur_gid" != "$gid" ]]; then
      echo "  ERROR: 组 $name 已存在但 gid=$cur_gid != $gid" >&2; exit 1
    fi
    echo "  $name (gid=$gid) exists"
  else
    sudo groupadd -g "$gid" "$name"
    echo "  + $name (gid=$gid)"
  fi
}

ensure_member() {
  local user=$1 grp=$2
  if id -nG "$user" | tr ' ' '\n' | grep -qx "$grp"; then
    echo "  $user already in $grp"
  else
    sudo usermod -aG "$grp" "$user"
    echo "  + $user -> $grp"
  fi
}

apply_dir() {
  local d=$1 owner=$2 mode=$3
  [[ -d "$d" ]] || { echo "  skip $d (不存在)"; return; }
  sudo setfacl -R -b "$d" 2>/dev/null || true
  sudo setfacl -R -k "$d" 2>/dev/null || true
  sudo chown -R "$owner" "$d"
  sudo chmod -R "$mode" "$d"
  # setgid 只打在目录上;文件 mode 已经被上面 -R 设过
  sudo find "$d" -type d -exec chmod g+s {} +
  echo "  $d  ($owner $mode + setgid)"
}

echo "[1/5] 组:"
ensure_group "$GID_CORE" "$GRP_CORE"
ensure_group "$GID_DATA" "$GRP_DATA"

echo "[2/5] 成员:"
for u in "${CORE_MEMBERS[@]}"; do ensure_member "$u" "$GRP_CORE"; done
for u in "${DATA_MEMBERS[@]}"; do ensure_member "$u" "$GRP_DATA"; done

echo "[3/5] 清理 alpha-author-* 残留:"
mapfile -t stale < <(getent group | awk -F: '$1 ~ /^alpha-author-/ {print $1}')
if (( ${#stale[@]} == 0 )); then
  echo "  无"
else
  for g in "${stale[@]}"; do
    members=$(getent group "$g" | awk -F: '{print $4}')
    IFS=',' read -ra arr <<< "$members"
    for m in "${arr[@]}"; do [[ -n "$m" ]] && sudo gpasswd -d "$m" "$g" >/dev/null && echo "  - $m from $g"; done
    sudo groupdel "$g"
    echo "  groupdel $g"
  done
fi

echo "[4/5] JFS (alpha_src / alpha_pnl / alpha_feature):"
apply_dir "$SRC"  "root:$GRP_CORE" "u=rwX,g=rX,o="
apply_dir "$PNL"  "root:$GRP_DATA" "u=rwX,g=rwX,o=rX"
apply_dir "$FEAT" "root:$GRP_DATA" "u=rwX,g=rwX,o=rX"

echo "[5/5] 本地 sidecar ($LOCAL):"
if [[ ! -d "$LOCAL" ]]; then
  echo "  $LOCAL 不存在,先跑 08-relocate-local-dirs.sh"; exit 1
fi

# 顶层:可穿越
sudo chown "root:$GRP_DATA" "$LOCAL"
sudo chmod 2755 "$LOCAL"
echo "  $LOCAL  (root:$GRP_DATA 2755)"

apply_dir "$STAGING" "root:$GRP_CORE" "u=rwX,g=rwX,o="
apply_dir "$DUMP"    "root:$GRP_DATA" "u=rwX,g=rwX,o=rX"

# recycle: sticky 顶层,子目录(已嵌套一层 unixId)按用户单独 chown
if [[ -d "$RECYCLE" ]]; then
  sudo chown root:root "$RECYCLE"
  sudo chmod 1755 "$RECYCLE"
  sudo chmod g-s "$RECYCLE"   # 清掉早期 09 留下的 setgid
  echo "  $RECYCLE  (root:root 1755 sticky)"
  for sub in "$RECYCLE"/*/; do
    [[ -d "$sub" ]] || continue
    uid=$(basename "$sub")
    if ! getent passwd "$uid" >/dev/null; then
      echo "  WARN: recycle/$uid 没对应系统用户,跳过"; continue
    fi
    pgrp=$(id -gn "$uid")
    sudo chown -R "$uid:$pgrp" "$sub"
    sudo chmod -R u=rwX,g=,o= "$sub"
    sudo find "$sub" -type d -exec chmod g-s {} +  # 清掉早期 setgid
    echo "  $sub  ($uid:$pgrp 0700)"
  done
else
  echo "  $RECYCLE 不存在,跳过"
fi

echo
echo "DONE."
echo
echo "前置:研究员 shell 必须 umask 0002,否则新文件 g-w 组写失效。"
echo "  echo 'umask 0002' | sudo tee /etc/profile.d/ops-umask.sh && sudo chmod 644 /etc/profile.d/ops-umask.sh"
echo
echo "组身份在当前 SSH session 没生效,验证用:"
echo "  sg $GRP_CORE -c 'id; ls $SRC'"
echo "  sg $GRP_DATA -c 'id; ls $PNL'"
