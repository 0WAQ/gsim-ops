#!/usr/bin/env bash
# 卸载 JuiceFS。默认不删数据;--purge 才真删 metadata + bucket + cache。

set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh

PURGE=false
[[ "${1:-}" == "--purge" ]] && PURGE=true

echo "[1/2] unmounting $JFS_MOUNT..."
if mountpoint -q "$JFS_MOUNT"; then
  juicefs umount "$JFS_MOUNT" || sudo umount "$JFS_MOUNT"
  echo "  unmounted"
else
  echo "  not mounted, skip"
fi

if ! $PURGE; then
  echo "[2/2] PRESERVE 模式: 卷 + bucket + cache 都保留"
  echo "  (重新挂载: 直接跑 ./03-format-mount.sh)"
  echo "  (彻底销毁: 重跑本脚本加 --purge)"
  exit 0
fi

echo "[2/2] PURGE 模式: 会销毁 JuiceFS 卷 + 删 MinIO bucket + 删 cache"
echo "  meta : $JFS_META_URL"
echo "  name : $JFS_NAME"
echo "  bucket: ${RCLONE_PROFILE}:${JFS_BUCKET}"
echo "  cache : $JFS_CACHE_DIR"
read -rp "  type 'yes' to confirm: " confirm
[[ "$confirm" == "yes" ]] || { echo "aborted"; exit 1; }

echo "  destroying JuiceFS volume..."
juicefs destroy --yes "$JFS_META_URL" "$(juicefs status "$JFS_META_URL" | awk -F\" '/UUID/{print $4; exit}')" \
  || echo "  warn: destroy failed (maybe already gone)"

echo "  purging MinIO bucket..."
TMP_CONF="$(mktemp -t juicefs-poc-rclone-XXXXXX.conf)"
trap 'rm -f "$TMP_CONF"' EXIT
cat > "$TMP_CONF" <<EOF
[poc]
type = s3
provider = Minio
endpoint = $MINIO_ENDPOINT
access_key_id = $MINIO_ACCESS_KEY
secret_access_key = $MINIO_SECRET_KEY
EOF
chmod 600 "$TMP_CONF"
rclone --config "$TMP_CONF" purge "poc:${JFS_BUCKET}" || true

echo "  removing local cache..."
sudo rm -rf "$JFS_CACHE_DIR"

echo "DONE."
