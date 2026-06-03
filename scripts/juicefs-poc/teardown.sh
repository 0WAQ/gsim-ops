#!/usr/bin/env bash
# 卸载 JuiceFS。默认不删数据;--purge 才真删 metadata + bucket + cache。

set -euo pipefail
cd "$(dirname "$0")"
source ./_lib.sh
source ./config.sh

PURGE=false
[[ "${1:-}" == "--purge" ]] && PURGE=true

info "[1/2] umount $JFS_MOUNT"
SVC="juicefs-${JFS_NAME}.service"
if systemctl list-unit-files 2>/dev/null | grep -q "^${SVC}"; then
  sudo systemctl stop "$SVC" 2>/dev/null || true
fi
if mountpoint -q "$JFS_MOUNT"; then
  juicefs umount "$JFS_MOUNT" || sudo umount "$JFS_MOUNT"
  info "  unmounted"
else
  info "  not mounted, skip"
fi

if ! $PURGE; then
  info "[2/2] PRESERVE: 卷 + bucket + cache 都保留"
  echo "  重新挂载: sudo systemctl start $SVC  (或 bootstrap.sh provision)"
  echo "  彻底销毁: 重跑加 --purge"
  exit 0
fi

info "[2/2] PURGE: 销毁卷 + 删 bucket + 删 cache"
echo "  meta  : $JFS_META_URL"
echo "  name  : $JFS_NAME"
echo "  bucket: ${RCLONE_PROFILE}:${JFS_BUCKET}"
echo "  cache : $JFS_CACHE_DIR"
read -rp "  type 'yes' to confirm: " confirm
[[ "$confirm" == "yes" ]] || { echo "aborted"; exit 1; }

info "  juicefs destroy"
juicefs destroy --yes "$JFS_META_URL" "$(juicefs status "$JFS_META_URL" | awk -F\" '/UUID/{print $4; exit}')" \
  || warn "  destroy failed (可能已没了)"

info "  purge MinIO bucket"
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

info "  remove local cache"
sudo rm -rf "$JFS_CACHE_DIR"

info "DONE."
