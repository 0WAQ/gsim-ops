#!/usr/bin/env bash
# 给 alphalib 应用两层组权限模型(终态):
#   - alpha-core (gid 59000) 核心组,直接读所有 alpha_src
#   - alpha-data (gid 59001) 数据池,直接读写 alpha_pnl/feature
#
# 写 alpha_src 必须以 root 身份(ops 通过 sudo 跑)。研究员零直接写权限;
# 研究员看自己代码也走 ops 接口,不依赖文件系统读权限。
#
# owner 一律 root,不依赖任何 uid。所有 enforcement 走 gid,gid 由本脚本固定。
# 幂等:可重复跑。会自动清理早期 per-author 实验的 alpha-author-* 组和 ACL。
#
# 设计前提:JuiceFS 卷已开 EnableACL,挂载后 setfacl 能用。

set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh

GID_CORE=59000
GID_DATA=59001
GRP_CORE="alpha-core"
GRP_DATA="alpha-data"

# core 组成员(超级读者)
CORE_MEMBERS=(wbai)
# alpha-data 成员(所有研究员)
DATA_MEMBERS=(wbai)

SRC_ROOT="$JFS_MOUNT/alpha_src"
PNL_ROOT="$JFS_MOUNT/alpha_pnl"
FEAT_ROOT="$JFS_MOUNT/alpha_feature"

# ---- 探针 1:JuiceFS 挂载点必须支持 ACL ----
if ! mountpoint -q "$JFS_MOUNT"; then
  echo "ERROR: $JFS_MOUNT 未挂载" >&2; exit 1
fi
probe="$JFS_MOUNT/.aclprobe.$$"
mkdir "$probe"
if ! setfacl -m u:nobody:r-x "$probe" 2>/dev/null; then
  rmdir "$probe"
  echo "ERROR: setfacl 在 $JFS_MOUNT 不支持(Operation not supported)" >&2
  echo "       需要 juicefs config --enable-acl 后 umount+remount" >&2
  exit 1
fi
rmdir "$probe"

# ---- 探针 2:alphalib.local 所在文件系统必须支持 ACL ----
# 早期版本曾在 ZFS pool 默认 acltype=off 时静默吃掉 setfacl 失败,留下"目录在/owner对/
# 但 default ACL 没打上"的隐性损坏。这里 fail-fast。
if [[ -d "$JFS_LOCAL_DIR" ]]; then
  probe_local="$JFS_LOCAL_DIR/.aclprobe.$$"
  mkdir "$probe_local"
  if ! setfacl -m u:nobody:r-x "$probe_local" 2>/dev/null; then
    rmdir "$probe_local"
    echo "ERROR: setfacl 在 $JFS_LOCAL_DIR 不支持(Operation not supported)" >&2
    echo "       若 $JFS_LOCAL_DIR 在 ZFS 上:" >&2
    echo "         sudo zfs set acltype=posixacl <dataset>" >&2
    echo "         sudo zfs set xattr=sa <dataset>" >&2
    echo "         sudo mount -o remount <mountpoint>" >&2
    echo "       然后重跑本脚本" >&2
    exit 1
  fi
  rmdir "$probe_local"
fi

# ---- helpers ----
ensure_group() {
  local gid=$1 name=$2
  local entry="" owner_by_gid=""
  if entry=$(getent group "$gid" 2>/dev/null); then
    owner_by_gid=$(printf '%s\n' "$entry" | cut -d: -f1)
  fi
  if [[ -n "$owner_by_gid" && "$owner_by_gid" != "$name" ]]; then
    echo "  ERROR: gid $gid 已被 '$owner_by_gid' 占用" >&2; exit 1
  fi
  if entry=$(getent group "$name" 2>/dev/null); then
    local cur_gid
    cur_gid=$(printf '%s\n' "$entry" | cut -d: -f3)
    if [[ "$cur_gid" != "$gid" ]]; then
      echo "  ERROR: 组 $name 已存在但 gid=$cur_gid != 期望 $gid" >&2; exit 1
    fi
    echo "  $name (gid=$gid) exists, skip"
  else
    sudo groupadd -g "$gid" "$name"
    echo "  + $name (gid=$gid)"
  fi
}

ensure_member() {
  local user=$1 grp=$2
  if id -nG "$user" | tr ' ' '\n' | grep -qx "$grp"; then
    echo "  $user already in $grp, skip"
  else
    sudo usermod -aG "$grp" "$user"
    echo "  + $user -> $grp"
  fi
}

echo "[1/6] 创建组(幂等):"
ensure_group "$GID_CORE" "$GRP_CORE"
ensure_group "$GID_DATA" "$GRP_DATA"

echo "[2/6] 用户加组(幂等):"
echo "  $GRP_CORE:"
for u in "${CORE_MEMBERS[@]}"; do ensure_member "$u" "$GRP_CORE"; done
echo "  $GRP_DATA:"
for u in "${DATA_MEMBERS[@]}"; do ensure_member "$u" "$GRP_DATA"; done

echo "[3/6] 清理 per-author 残留(早期 A 模式的 alpha-author-*):"
stale_groups=()
while IFS= read -r line; do
  [[ -n "$line" ]] && stale_groups+=("$line")
done < <(getent group | awk -F: '$1 ~ /^alpha-author-/ {print $1}')

if (( ${#stale_groups[@]} == 0 )); then
  echo "  无残留"
else
  for grp in "${stale_groups[@]}"; do
    members=$(getent group "$grp" | awk -F: '{print $4}')
    if [[ -n "$members" ]]; then
      IFS=',' read -ra arr <<< "$members"
      for m in "${arr[@]}"; do
        [[ -z "$m" ]] && continue
        sudo gpasswd -d "$m" "$grp" >/dev/null
        echo "  - $m from $grp"
      done
    fi
    sudo groupdel "$grp"
    echo "  groupdel $grp"
  done
fi

echo "[4/6] 设置 alpha_pnl / alpha_feature (root:$GRP_DATA 2770,全员读写):"
for d in "$PNL_ROOT" "$FEAT_ROOT"; do
  [[ -d "$d" ]] || { echo "  skip $d (不存在)"; continue; }
  sudo setfacl -R -b "$d" 2>/dev/null || true
  sudo setfacl -R -k "$d" 2>/dev/null || true
  sudo chown -R "root:$GRP_DATA" "$d"
  sudo chmod -R u=rwX,g=rwX,o= "$d"
  sudo find "$d" -type d -exec chmod g+s {} +
  sudo setfacl -R -d -m "g:$GRP_DATA:rwx" "$d"
  echo "  $d set"
done

echo "[5/6] 设置 alpha_src 整棵 (root:$GRP_CORE 2750,研究员零直接写):"
if [[ -d "$SRC_ROOT" ]]; then
  # 清旧 ACL(A 模式给过 alpha-data / alpha-core 之类的 named entries)
  sudo setfacl -R -b "$SRC_ROOT" 2>/dev/null || true
  sudo setfacl -R -k "$SRC_ROOT" 2>/dev/null || true
  sudo chown -R "root:$GRP_CORE" "$SRC_ROOT"
  # 目录 2750 = setgid + g rx,文件 640
  sudo chmod -R u=rwX,g=rX,o= "$SRC_ROOT"
  sudo find "$SRC_ROOT" -type d -exec chmod g+s {} +
  echo "  $SRC_ROOT  (root:$GRP_CORE 2750)"
else
  echo "  $SRC_ROOT 不存在,跳过"
fi

echo "[6/6] 设置本地 sidecar $JFS_LOCAL_DIR (root:$GRP_DATA 2770,所有研究员读写):"
if [[ -d "$JFS_LOCAL_DIR" ]]; then
  sudo setfacl -R -b "$JFS_LOCAL_DIR" 2>/dev/null || true
  sudo setfacl -R -k "$JFS_LOCAL_DIR" 2>/dev/null || true
  sudo chown -R "root:$GRP_DATA" "$JFS_LOCAL_DIR"
  sudo chmod -R u=rwX,g=rwX,o= "$JFS_LOCAL_DIR"
  sudo find "$JFS_LOCAL_DIR" -type d -exec chmod g+s {} +
  sudo setfacl -R -d -m "g:$GRP_DATA:rwx" "$JFS_LOCAL_DIR"
  echo "  $JFS_LOCAL_DIR set"
else
  echo "  $JFS_LOCAL_DIR 不存在,跳过(先跑 08-relocate-local-dirs.sh)"
fi

echo
echo "DONE."
echo
echo "wbai 当前 shell 没获得新组身份。临时切换验证:"
echo "  exec sg $GRP_CORE -c bash"
echo "  id   # 应看到 $GRP_CORE / $GRP_DATA;不应再看到 alpha-author-wbai"
