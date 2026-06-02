#!/usr/bin/env bash
# Common config for JuiceFS PoC. Source this; do not run directly.
# 所有其他脚本都 source 这个文件来拿环境变量。
# MinIO 凭证从 ~/.config/rclone/rclone.conf 读,不硬编码。

set -euo pipefail

# ---- 改这里:rclone.conf 里的 profile 段名(凭证回退用) ----
RCLONE_PROFILE="${RCLONE_PROFILE:-39000}"

RCLONE_CONF="${HOME}/.config/rclone/rclone.conf"
if [[ ! -f "$RCLONE_CONF" ]]; then
  echo "warn: $RCLONE_CONF not found" >&2
fi

# 粗糙 ini 解析,只读指定 section 里的 key
_rclone_get() {
  [[ -f "$RCLONE_CONF" ]] || { echo ""; return; }
  awk -v section="[$RCLONE_PROFILE]" -v key="$1" '
    $0 == section {in_section=1; next}
    /^\[/ {in_section=0}
    in_section && $1 == key {
      # 取等号后内容,处理 "key = value" 也处理 "key=value"
      sub(/^[^=]*=[ \t]*/, "", $0); print; exit
    }
  ' "$RCLONE_CONF"
}

# 优先级: 环境变量 > rclone.conf。
# 支持 MINIO_ROOT_USER/PASSWORD (官方名) 和 MINIO_ACCESS_KEY/SECRET_KEY 两套命名。
export MINIO_ENDPOINT="${MINIO_ENDPOINT:-$(_rclone_get endpoint)}"
export MINIO_ACCESS_KEY="${MINIO_ROOT_USER:-${MINIO_ACCESS_KEY:-$(_rclone_get access_key_id)}}"
export MINIO_SECRET_KEY="${MINIO_ROOT_PASSWORD:-${MINIO_SECRET_KEY:-$(_rclone_get secret_access_key)}}"
export RCLONE_PROFILE

if [[ -z "$MINIO_ENDPOINT" || -z "$MINIO_ACCESS_KEY" || -z "$MINIO_SECRET_KEY" ]]; then
  echo "error: MinIO credentials not found." >&2
  echo "  set env vars: MINIO_ENDPOINT / MINIO_ROOT_USER / MINIO_ROOT_PASSWORD" >&2
  echo "  or fill in:   $RCLONE_CONF [$RCLONE_PROFILE]" >&2
  exit 1
fi

# ---- JuiceFS PoC 参数 ----
export JFS_NAME="${JFS_NAME:-alphalib}"
export JFS_BUCKET="${JFS_BUCKET:-alphalib-juicefs}"
export JFS_META_URL="${JFS_META_URL:-redis://127.0.0.1:6379/0}"
export JFS_MOUNT="${JFS_MOUNT:-/tank/vault/alphalib}"
export JFS_CACHE_DIR="${JFS_CACHE_DIR:-/tank/vault/juicefs-cache}"
export JFS_CACHE_SIZE_MB="${JFS_CACHE_SIZE_MB:-512000}"   # 500 GB

# 如果直接 source(非顶层调用),不打印
if [[ "${BASH_SOURCE[0]}" == "${0}" ]] || [[ "${1:-}" == "--show" ]]; then
  cat <<EOF
=== JuiceFS PoC config ===
MinIO endpoint : $MINIO_ENDPOINT
MinIO bucket   : $JFS_BUCKET
JuiceFS name   : $JFS_NAME
Mount point    : $JFS_MOUNT
Cache dir      : $JFS_CACHE_DIR (${JFS_CACHE_SIZE_MB} MB)
Meta engine    : $JFS_META_URL
==========================
EOF
fi
