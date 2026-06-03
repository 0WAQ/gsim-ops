#!/usr/bin/env bash
# 数据迁移: /mnt/storage/alphalib/{alpha_src,alpha_pnl,alpha_feature}
#       -> /tank/vault/alphalib/{alpha_src,alpha_pnl,alpha_feature}
#
# 步骤:
#   [1/5] pre-flight (mount / 源存在 / writeback 当前队列 / cache 空间)
#   [2/5] rsync -a (增量,可重跑)
#   [3/5] 等 writeback 队列清零 (juicefs_staging_blocks=0),保证 chunk 都到 S3
#   [4/5] 修正 ownership 到权限模型:
#         alpha_src:  chown -R :alpha-core   (保留作者 user)
#         alpha_pnl / alpha_feature: chown -R root:alpha-data
#         模式按目录类型套 (dir 含 setgid;文件 644/640)
#   [5/5] 对账 (文件数 + du -sb + 抽样 md5)
#
# 用法:
#   sudo -E bash 05-migrate.sh                       # 三个目录全量
#   sudo -E bash 05-migrate.sh --only alpha_src      # 只一个
#   sudo -E bash 05-migrate.sh --only alpha_pnl,alpha_feature
#   sudo -E bash 05-migrate.sh --dry-run             # rsync --dry-run + 不动 ownership
#   sudo -E bash 05-migrate.sh --skip-verify         # 跳 [5/5]
#   SRC=/path sudo -E bash 05-migrate.sh             # 自定义源
#
# 大量数据迁移建议:
#   nohup sudo -E bash 05-migrate.sh > /tmp/jfs-migrate.log 2>&1 &
#   tail -f /tmp/jfs-migrate.log
#
# 幂等。中断重跑安全 (rsync 增量, chown 重复=no-op)。

set -euo pipefail
cd "$(dirname "$0")"
source ./_lib.sh
source ./config.sh

SRC="${SRC:-/mnt/storage/alphalib}"
DRY_RUN=0
SKIP_VERIFY=0
ONLY=""
SAMPLE_N="${SAMPLE_N:-10}"

while (( $# )); do
  case "$1" in
    --dry-run)     DRY_RUN=1; shift;;
    --skip-verify) SKIP_VERIFY=1; shift;;
    --only)        ONLY=$2; shift 2;;
    --src)         SRC=$2; shift 2;;
    -h|--help)     sed -n '2,/^$/p' "$0"; exit 0;;
    *)             err "未知参数: $1";;
  esac
done

require_sudo
require_mountpoint "$JFS_MOUNT"
require_bin rsync
require_bin md5sum
require_bin shuf

ALL_DIRS=(alpha_src alpha_pnl alpha_feature)
if [[ -n "$ONLY" ]]; then
  IFS=',' read -ra DIRS <<< "$ONLY"
  for d in "${DIRS[@]}"; do
    [[ " ${ALL_DIRS[*]} " == *" $d "* ]] || err "未知目录: $d (可选 ${ALL_DIRS[*]})"
  done
else
  DIRS=("${ALL_DIRS[@]}")
fi

GRP_CORE=alpha-core
GRP_DATA=alpha-data
for g in $GRP_CORE $GRP_DATA; do
  getent group "$g" >/dev/null || err "组 $g 不存在,先跑 02-layout.sh"
done

STATS_FILE="$JFS_MOUNT/.stats"
get_stat() { awk -v k="juicefs_$1" '$1 == k {print $2}' "$STATS_FILE" 2>/dev/null; }

# ============================================================
# [1/5] pre-flight
# ============================================================
info "==> [1/5] pre-flight"
[[ -d "$SRC" ]] || err "源 $SRC 不存在"

TOTAL_SRC_FILES=0
TOTAL_SRC_BYTES=0
declare -A SRC_FILES SRC_BYTES
for d in "${DIRS[@]}"; do
  [[ -d "$SRC/$d" ]] || err "源缺 $d: $SRC/$d"
  n=$(find "$SRC/$d" -type f 2>/dev/null | wc -l)
  b=$(du -sb "$SRC/$d" 2>/dev/null | awk '{print $1}')
  SRC_FILES[$d]=$n
  SRC_BYTES[$d]=$b
  TOTAL_SRC_FILES=$((TOTAL_SRC_FILES + n))
  TOTAL_SRC_BYTES=$((TOTAL_SRC_BYTES + b))
  info "  src $d: $n files / $(numfmt --to=iec $b)"
done
info "  src 合计: $TOTAL_SRC_FILES files / $(numfmt --to=iec $TOTAL_SRC_BYTES)"

# 当前 writeback 队列(避免和别的迁移撞)
sb=$(get_stat staging_blocks)
[[ "${sb:-0}" != "0" ]] && warn "  writeback 已有 $sb 个 staging block (别的迁移没结束?)"

# cache 可用空间提示(writeback 模式下 cache 顶不住会写回 S3,正确性 OK)
free_b=$(df -B1 --output=avail "$JFS_CACHE_DIR" | tail -1)
info "  cache 可用 $(numfmt --to=iec $free_b) (上限 ${JFS_CACHE_SIZE_MB} MB)"

# ============================================================
# [2/5] rsync
# ============================================================
echo
info "==> [2/5] rsync $SRC -> $JFS_MOUNT"
RSYNC_FLAGS=(-a --info=progress2,stats2)
(( DRY_RUN )) && RSYNC_FLAGS+=(--dry-run)

for d in "${DIRS[@]}"; do
  echo
  info "  ---- $d ----"
  log="/tmp/jfs-migrate-${d}.log"
  if rsync "${RSYNC_FLAGS[@]}" "$SRC/$d/" "$JFS_MOUNT/$d/" 2>&1 | tee "$log" | tail -n 3; then
    info "  $d 同步完成 (log: $log)"
  else
    err "  $d 同步失败,详见 $log"
  fi
done

if (( DRY_RUN )); then
  echo
  info "DRY RUN OK。实际跑去掉 --dry-run。"
  exit 0
fi

# ============================================================
# [3/5] 等 writeback drain
# ============================================================
echo
info "==> [3/5] 等 writeback 队列清零 (避免 cache 里有未上传 chunk = 关机丢数据)"
DRAIN_START=$(date +%s)
while true; do
  s=$(get_stat staging_blocks)
  w=$(get_stat staging_writing_blocks)
  s=${s:-0}; w=${w:-0}
  if [[ "$s" == "0" && "$w" == "0" ]]; then
    break
  fi
  info "  staging=$s writing=$w 等..."
  sleep 30
done
info "  drain 完成 ($(( $(date +%s) - DRAIN_START ))s)"

# ============================================================
# [4/5] 修正 ownership
# ============================================================
echo
info "==> [4/5] ownership 修正到权限模型"
for d in "${DIRS[@]}"; do
  T="$JFS_MOUNT/$d"
  case "$d" in
    alpha_src)
      info "  $d: chown -R :$GRP_CORE (保留 author user)"
      sudo chown -R ":$GRP_CORE" "$T"
      sudo find "$T" -type d -exec chmod 2750 {} +    # rwxr-s---
      sudo find "$T" -type f -exec chmod 0640 {} +    # rw-r-----
      ;;
    alpha_pnl|alpha_feature)
      info "  $d: chown -R root:$GRP_DATA"
      sudo chown -R "root:$GRP_DATA" "$T"
      sudo find "$T" -type d -exec chmod 2775 {} +    # rwxrwsr-x
      sudo find "$T" -type f -exec chmod 0664 {} +    # rw-rw-r--
      ;;
  esac
done

# ============================================================
# [5/5] 对账
# ============================================================
if (( SKIP_VERIFY )); then
  echo
  warn "==> [5/5] 跳过 (--skip-verify)"
  echo
  info "DONE (未验证)。"
  exit 0
fi

echo
info "==> [5/5] 对账"
FAIL=0
for d in "${DIRS[@]}"; do
  echo
  info "  ---- $d ----"
  src_n=${SRC_FILES[$d]}
  src_b=${SRC_BYTES[$d]}
  dst_n=$(find "$JFS_MOUNT/$d" -type f 2>/dev/null | wc -l)
  dst_b=$(du -sb "$JFS_MOUNT/$d" 2>/dev/null | awk '{print $1}')

  if [[ "$src_n" == "$dst_n" ]]; then
    info "    file count: $src_n == $dst_n ✓"
  else
    warn "    file count: src $src_n != dst $dst_n ✗"
    FAIL=$((FAIL+1))
  fi
  if [[ "$src_b" == "$dst_b" ]]; then
    info "    bytes:      $(numfmt --to=iec $src_b) == $(numfmt --to=iec $dst_b) ✓"
  else
    warn "    bytes:      src $src_b != dst $dst_b ✗"
    FAIL=$((FAIL+1))
  fi

  # 抽样 md5
  info "    md5 抽样 $SAMPLE_N 文件"
  mapfile -t samples < <(find "$SRC/$d" -type f 2>/dev/null | shuf -n "$SAMPLE_N")
  mm=0
  for f in "${samples[@]:-}"; do
    [[ -z "$f" ]] && continue
    rel="${f#$SRC/$d/}"
    src_md5=$(md5sum "$f" 2>/dev/null | awk '{print $1}')
    dst_md5=$(md5sum "$JFS_MOUNT/$d/$rel" 2>/dev/null | awk '{print $1}')
    if [[ -z "$dst_md5" ]]; then
      warn "      missing: $rel"
      mm=$((mm+1))
    elif [[ "$src_md5" != "$dst_md5" ]]; then
      warn "      mismatch: $rel ($src_md5 vs $dst_md5)"
      mm=$((mm+1))
    fi
  done
  if (( mm )); then
    warn "    md5: $mm/${#samples[@]} 不一致 ✗"
    FAIL=$((FAIL+1))
  else
    info "    md5: ${#samples[@]}/${#samples[@]} 一致 ✓"
  fi
done

echo
if (( FAIL )); then
  err "对账失败 $FAIL 项"
else
  info "DONE. 对账全过。"
fi
