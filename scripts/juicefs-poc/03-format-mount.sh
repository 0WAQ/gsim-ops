#!/usr/bin/env bash
# 格式化 JuiceFS 卷(一次性)+ 挂载。

set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh

BUCKET_URL="${MINIO_ENDPOINT}/${JFS_BUCKET}"

echo "[1/3] formatting JuiceFS volume '$JFS_NAME'..."
echo "  (juicefs format 是幂等的:已格式化则跳过)"
juicefs format \
  --storage minio \
  --bucket "$BUCKET_URL" \
  --access-key "$MINIO_ACCESS_KEY" \
  --secret-key "$MINIO_SECRET_KEY" \
  "$JFS_META_URL" \
  "$JFS_NAME"

echo "[2/3] preparing mount point '$JFS_MOUNT'..."
if [[ ! -d "$JFS_MOUNT" ]]; then
  sudo mkdir -p "$JFS_MOUNT"
  sudo chown "$USER:$(id -gn)" "$JFS_MOUNT"
fi

echo "[3/3] mounting..."
if mountpoint -q "$JFS_MOUNT"; then
  echo "  already mounted"
else
  juicefs mount \
    --cache-dir "$JFS_CACHE_DIR" \
    --cache-size "$JFS_CACHE_SIZE_MB" \
    --writeback \
    --background \
    "$JFS_META_URL" \
    "$JFS_MOUNT"
  sleep 1
fi

echo
echo "Mount status:"
mount | grep "$JFS_MOUNT" || true
df -h "$JFS_MOUNT" || true

echo
echo "Sanity ls:"
ls -la "$JFS_MOUNT" || true

echo
echo "DONE. Next: ./04-verify-basic.sh"
