#!/usr/bin/env bash
# 创建新 MinIO bucket 和本地 cache 目录。幂等。
# 凭证通过临时 rclone config 注入,绕开默认 rclone.conf 里权限受限的 profile。

set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh

# 这里真用 MinIO 凭证,缺一不可
if [[ -z "$MINIO_ENDPOINT" || -z "$MINIO_ACCESS_KEY" || -z "$MINIO_SECRET_KEY" ]]; then
  echo "ERROR: 02-prepare 需要 MinIO 凭证。设 MINIO_ROOT_USER/MINIO_ROOT_PASSWORD 或 rclone.conf" >&2
  exit 1
fi

# 写一份临时 rclone 配置,用 trap 兜底清理
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
RCLONE="rclone --config $TMP_CONF"

echo "[1/3] checking/creating MinIO bucket '$JFS_BUCKET'..."
if $RCLONE lsd "poc:${JFS_BUCKET}" >/dev/null 2>&1; then
  echo "  bucket exists, skip"
else
  $RCLONE mkdir "poc:${JFS_BUCKET}"
  echo "  mkdir issued"
fi

echo "[2/3] verifying real write access (rclone mkdir 可能被假装成功)..."
PROBE_KEY="_poc_probe_$$.txt"
echo "hello juicefs poc" | $RCLONE rcat "poc:${JFS_BUCKET}/${PROBE_KEY}"
$RCLONE ls "poc:${JFS_BUCKET}/${PROBE_KEY}"
$RCLONE delete "poc:${JFS_BUCKET}/${PROBE_KEY}"
echo "  write/list/delete all ok"

echo "[3/3] creating cache dir '$JFS_CACHE_DIR'..."
if [[ -d "$JFS_CACHE_DIR" ]]; then
  echo "  exists, skip"
else
  sudo mkdir -p "$JFS_CACHE_DIR"
  sudo chown "$USER:$(id -gn)" "$JFS_CACHE_DIR"
  echo "  created (owned by $USER)"
fi

echo
echo "MinIO reachability:"
if curl -sS --max-time 5 "${MINIO_ENDPOINT}/minio/health/live" -o /dev/null -w "  HTTP %{http_code}\n"; then
  :
else
  echo "  WARN: MinIO endpoint check failed (might still work via S3 API)"
fi

echo
echo "DONE. Next: ./03-format-mount.sh"
