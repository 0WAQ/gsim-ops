#!/usr/bin/env bash
# JuiceFS PoC 共享配置。source 用,不要直接跑。
# MinIO 凭证从 ~/.config/rclone/rclone.conf 读,不硬编码。

set -euo pipefail

RCLONE_PROFILE="${RCLONE_PROFILE:-39000}"
RCLONE_CONF="${HOME}/.config/rclone/rclone.conf"

# 粗糙 ini 解析,只读指定 section 里的 key
_rclone_get() {
  [[ -f "$RCLONE_CONF" ]] || { echo ""; return; }
  awk -v section="[$RCLONE_PROFILE]" -v key="$1" '
    $0 == section {in_section=1; next}
    /^\[/ {in_section=0}
    in_section && $1 == key { sub(/^[^=]*=[ \t]*/, "", $0); print; exit }
  ' "$RCLONE_CONF"
}

# 优先级: 环境变量 > rclone.conf。MINIO_ROOT_USER/PASSWORD 优先于 _ACCESS_KEY/_SECRET_KEY。
export MINIO_ENDPOINT="${MINIO_ENDPOINT:-$(_rclone_get endpoint)}"
export MINIO_ACCESS_KEY="${MINIO_ROOT_USER:-${MINIO_ACCESS_KEY:-$(_rclone_get access_key_id)}}"
export MINIO_SECRET_KEY="${MINIO_ROOT_PASSWORD:-${MINIO_SECRET_KEY:-$(_rclone_get secret_access_key)}}"
export RCLONE_PROFILE

# Client 节点不需要 MinIO 凭证,只在 bootstrap provision 阶段会硬要求
if [[ -z "$MINIO_ENDPOINT" || -z "$MINIO_ACCESS_KEY" || -z "$MINIO_SECRET_KEY" ]]; then
  echo "warn: MinIO 凭证未设(env 或 $RCLONE_CONF [$RCLONE_PROFILE])。provision 阶段会要求。" >&2
fi

export JFS_NAME="${JFS_NAME:-alphalib}"
export JFS_BUCKET="${JFS_BUCKET:-alphalib-juicefs}"
export JFS_META_URL="${JFS_META_URL:-redis://127.0.0.1:6379/0}"
export JFS_MOUNT="${JFS_MOUNT:-/tank/vault/alphalib}"
export JFS_LOCAL_DIR="${JFS_LOCAL_DIR:-${JFS_MOUNT}.local}"
export JFS_CACHE_DIR="${JFS_CACHE_DIR:-/tank/vault/juicefs-cache}"
export JFS_CACHE_SIZE_MB="${JFS_CACHE_SIZE_MB:-512000}"

if [[ "${BASH_SOURCE[0]}" == "${0}" ]] || [[ "${1:-}" == "--show" ]]; then
  cat <<EOF
=== JuiceFS PoC config ===
MinIO endpoint : $MINIO_ENDPOINT
MinIO bucket   : $JFS_BUCKET
JuiceFS name   : $JFS_NAME
Mount point    : $JFS_MOUNT
Local sidecar  : $JFS_LOCAL_DIR
Cache dir      : $JFS_CACHE_DIR (${JFS_CACHE_SIZE_MB} MB)
Meta engine    : $JFS_META_URL
==========================
EOF
fi
