#!/usr/bin/env bash
# MinIO bucket + cache dir + juicefs format + 临时挂载。
# 只主节点跑;client 节点不需要 MinIO 凭证。

set -euo pipefail
cd "$(dirname "$0")"
source ./_lib.sh
source ./config.sh

require_sudo
require_bin juicefs "curl -sSL https://d.juicefs.com/install | sudo sh -"
require_bin rclone  "apt install rclone"

[[ -n "$MINIO_ENDPOINT" && -n "$MINIO_ACCESS_KEY" && -n "$MINIO_SECRET_KEY" ]] \
  || err "需要 MinIO 凭证(MINIO_ROOT_USER/MINIO_ROOT_PASSWORD 或 rclone.conf)"

# 临时 rclone 配置,避开默认 conf 里权限受限的 profile
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

info "[1/4] bucket '$JFS_BUCKET'"
if $RCLONE lsd "poc:${JFS_BUCKET}" >/dev/null 2>&1; then
  info "  已存在,跳过"
else
  $RCLONE mkdir "poc:${JFS_BUCKET}"
  info "  mkdir issued"
fi
# rclone mkdir 在 no_check_bucket=true 时会假装成功,走真 PutObject 兜底
PROBE_KEY="_poc_probe_$$.txt"
echo "hello juicefs poc" | $RCLONE rcat "poc:${JFS_BUCKET}/${PROBE_KEY}"
$RCLONE ls "poc:${JFS_BUCKET}/${PROBE_KEY}" >/dev/null
$RCLONE delete "poc:${JFS_BUCKET}/${PROBE_KEY}"
info "  write/list/delete ok"

info "[2/4] cache dir '$JFS_CACHE_DIR'"
if [[ -d "$JFS_CACHE_DIR" ]]; then
  info "  已存在,跳过"
else
  sudo mkdir -p "$JFS_CACHE_DIR"
  sudo chown "${SUDO_USER:-$USER}:$(id -gn "${SUDO_USER:-$USER}")" "$JFS_CACHE_DIR"
  info "  created"
fi

info "[3/4] juicefs format (幂等)"
juicefs format \
  --storage minio \
  --bucket "${MINIO_ENDPOINT}/${JFS_BUCKET}" \
  --access-key "$MINIO_ACCESS_KEY" \
  --secret-key "$MINIO_SECRET_KEY" \
  "$JFS_META_URL" \
  "$JFS_NAME"

info "[4/4] 临时挂载 $JFS_MOUNT"
if [[ ! -d "$JFS_MOUNT" ]]; then
  sudo mkdir -p "$JFS_MOUNT"
  sudo chown "${SUDO_USER:-$USER}:$(id -gn "${SUDO_USER:-$USER}")" "$JFS_MOUNT"
fi
if mountpoint -q "$JFS_MOUNT"; then
  info "  已挂载"
else
  juicefs mount \
    --cache-dir "$JFS_CACHE_DIR" \
    --cache-size "$JFS_CACHE_SIZE_MB" \
    --writeback --background \
    "$JFS_META_URL" "$JFS_MOUNT"
  sleep 1
  info "  mounted"
fi

echo
info "DONE. 下一步: sudo -E bash 02-layout.sh"
