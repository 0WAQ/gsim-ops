#!/usr/bin/env bash
# 把 alpha_dump / staging / recycle 从 JuiceFS 挂载里搬出去,改成指向本地后备
# 目录的 symlink。这三类是每机各自一份的本地概念,不该走 JuiceFS。
# 同时清掉 PoC 早期对照测试残留 (_poc_git_local)。
# 幂等:每一步先检测当前状态,已就位则跳过。

set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh

LOCAL_DIRS=(alpha_dump staging recycle)
POC_REMNANT="$(dirname "$JFS_MOUNT")/_poc_git_local"

# 在 sudo -E 下 $USER 会变成 root;用 SUDO_USER 拿回原用户身份
REAL_USER="${SUDO_USER:-$USER}"
REAL_GROUP="$(id -gn "$REAL_USER")"

echo "[1/4] 检查 $JFS_MOUNT 已挂载 ..."
if ! mountpoint -q "$JFS_MOUNT"; then
  echo "  ERROR: $JFS_MOUNT 未挂载,先跑 03-format-mount.sh" >&2
  exit 1
fi

echo "[2/4] 准备本地后备目录 $JFS_LOCAL_DIR (owner: $REAL_USER:$REAL_GROUP) ..."
sudo mkdir -p "$JFS_LOCAL_DIR"
for d in "${LOCAL_DIRS[@]}"; do
  sudo mkdir -p "$JFS_LOCAL_DIR/$d"
done
sudo chown -R "$REAL_USER:$REAL_GROUP" "$JFS_LOCAL_DIR"
sudo chmod -R u=rwX,g=rwX,o=rX "$JFS_LOCAL_DIR"
echo "  ready"

echo "[3/4] 在挂载点里把 ${LOCAL_DIRS[*]} 替换为 symlink ..."
for d in "${LOCAL_DIRS[@]}"; do
  target="$JFS_MOUNT/$d"
  expected="$JFS_LOCAL_DIR/$d"
  if [[ -L "$target" ]]; then
    cur="$(readlink "$target")"
    if [[ "$cur" == "$expected" ]]; then
      echo "  $d  symlink ok, skip"
      continue
    fi
    echo "  $d  symlink points to $cur, fixing"
    rm "$target"
  elif [[ -d "$target" ]]; then
    # 只允许替换"空目录树"(没有任何 regular file)
    if find "$target" -type f -print -quit | grep -q .; then
      echo "  ERROR: $target 含有文件,拒绝替换为 symlink(请人工确认数据)" >&2
      exit 1
    fi
    rm -rf "$target"
  fi
  ln -s "$expected" "$target"
  echo "  $d  -> $expected"
done

echo "[4/4] 清理 PoC 早期对照仓库 $POC_REMNANT ..."
if [[ -d "$POC_REMNANT" ]]; then
  sudo rm -rf "$POC_REMNANT"
  echo "  removed"
else
  echo "  not present, skip"
fi

echo
echo "DONE. 验证:"
echo "  ls -la $JFS_MOUNT"
echo "  ls -la $JFS_LOCAL_DIR"
