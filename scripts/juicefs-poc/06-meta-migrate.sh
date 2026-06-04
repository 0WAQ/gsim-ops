#!/usr/bin/env bash
# 把 JuiceFS metadata 从共用的 6379/db0 迁到独立实例 6380/db0。
#
# 前置:跑过 06-redis-jfs.sh,6380 已起 + AOF on + 空实例。
#
# 步骤 (apply 模式):
#   [1/8] investigate -- scan 6379 所有 keys, 按 prefix+type 分组
#         JuiceFS schema 是白名单 (setting / nextinode / i* / c*_* / d* / ...)
#         非白名单的全列出 -- 默认全部排除, 不迁
#   [2/8] stop juicefs-alphalib.service (避免 SCAN/MIGRATE 时数据在动)
#   [3/8] 备份 6380 dump.rdb (空实例 size 很小, 留个回滚锚点)
#   [4/8] MIGRATE in batches of 1000 keys, 失败 batch 写到 /tmp/jfs-migrate-failed.txt
#   [5/8] 验证: 6380 dbsize == 之前 6379 的 JFS keys 数; 6379 dbsize == 排除数
#   [6/8] 改 /etc/juicefs-poc.env 加 JFS_META_HOST/PORT (覆盖 config.sh 默认)
#         + 改 04-systemd 渲染时用 alphalib-jfs.env 而不是 alphalib.env
#   [7/8] 重渲染 + start juicefs-alphalib.service
#   [8/8] mountpoint + 抽样 md5 alpha_feature 文件
#
# 用法:
#   sudo -E bash 06-meta-migrate.sh --investigate     # 只看, 不动
#   sudo -E bash 06-meta-migrate.sh --dry-run         # apply 全程除了真 MIGRATE
#   sudo -E bash 06-meta-migrate.sh                   # 真跑 (apply)
#
# JFS mount 中断:[2/8]~[7/8] 之间, ~30-60s。期间在 mount 上 ls/read 会 hang。

set -euo pipefail
cd "$(dirname "$0")"
source ./_lib.sh
source ./config.sh

require_sudo
require_systemd
require_bin redis-cli

ENV_BIZ=/etc/juicefs/${JFS_NAME}.env          # 老 6379 (alphalib biz)
ENV_JFS=/etc/juicefs/${JFS_NAME}-jfs.env      # 新 6380 (JFS 专用)
JFS_SVC="juicefs-${JFS_NAME}.service"
JFS_REDIS_SVC=redis-jfs.service
HOST_ENV=/etc/juicefs-poc.env                 # config.sh 自动 source 这个
FAILED_LOG=/tmp/jfs-migrate-failed.txt

PORT_BIZ=6379
PORT_JFS=6380
HOST=127.0.0.1
BATCH=1000
TIMEOUT_MS=10000

# ============================================================
MODE=apply
while (( $# )); do
  case "$1" in
    --investigate) MODE=investigate; shift;;
    --dry-run)     MODE=dry-run; shift;;
    -h|--help) sed -n '2,/^$/p' "$0"; exit 0;;
    *) err "未知参数: $1";;
  esac
done

# ============================================================
# 前置自检
# ============================================================
sudo test -f "$ENV_BIZ" || err "缺 $ENV_BIZ (跑过 03-redis.sh 才有)"
sudo test -f "$ENV_JFS" || err "缺 $ENV_JFS (跑 06-redis-jfs.sh 先)"
PASS_BIZ=$(sudo grep -oP 'META_PASSWORD=\K.*' "$ENV_BIZ")
PASS_JFS=$(sudo grep -oP 'META_PASSWORD=\K.*' "$ENV_JFS")
[[ -n "$PASS_BIZ" && -n "$PASS_JFS" ]] || err "密码读不出来"

systemctl is-active --quiet "$JFS_REDIS_SVC" || err "$JFS_REDIS_SVC 没在跑 (先跑 06-redis-jfs.sh)"

RC_BIZ() { redis-cli -h $HOST -p $PORT_BIZ -a "$PASS_BIZ" --no-auth-warning "$@"; }
RC_JFS() { redis-cli -h $HOST -p $PORT_JFS -a "$PASS_JFS" --no-auth-warning "$@"; }

RC_BIZ ping 2>/dev/null | grep -q PONG || err "6379 AUTH 失败"
RC_JFS ping 2>/dev/null | grep -q PONG || err "6380 AUTH 失败"

# ============================================================
# JuiceFS Redis schema 白名单 (来自 juicefs/pkg/meta/redis.go)
# 单 key (固定名) + 前缀模式
# ============================================================
JFS_FIXED_KEYS=(
  setting
  nextinode nextchunk nextsession nextcleanupSliceset nexttrash
  totalInodes usedSpace
  sliceRef delfiles delSlices
  sessionHB sessions     # JuiceFS sessions zset (注意跟 biz 的 allSessions 不同 key)
  lockedi
  allSessions            # 历史/兼容
)
# 前缀:i{inode} d{inode} c{inode}_{index} sustained{sid} session{sid} flock{inode}
#       plock{inode} symlink{inode} xattr{inode} clean{inode} acl
JFS_PREFIXES='^(i[0-9]|d[0-9]|c[0-9]+_|sustained[0-9]|session[0-9]|flock[0-9]|plock[0-9]|symlink[0-9]|xattr[0-9]|clean[0-9]|acl|trash[0-9])'

is_jfs_key() {
  local k=$1
  for fixed in "${JFS_FIXED_KEYS[@]}"; do
    [[ "$k" == "$fixed" ]] && return 0
  done
  [[ "$k" =~ $JFS_PREFIXES ]] && return 0
  return 1
}

# ============================================================
hr() { printf '\n=== %s ===\n' "$*"; }
hr "[1/8] investigate 6379 (alphalib biz) 当前 keys 分布"
# ============================================================
TOTAL_BEFORE=$(RC_BIZ dbsize)
info "  6379 dbsize=$TOTAL_BEFORE"
info "  6380 dbsize=$(RC_JFS dbsize)"

# 列所有 keys
ALL_KEYS_FILE=/tmp/jfs-keys-all.txt
JFS_KEYS_FILE=/tmp/jfs-keys-tomigrate.txt
EXCLUDED_FILE=/tmp/jfs-keys-excluded.txt
RC_BIZ --scan > "$ALL_KEYS_FILE"
N_TOTAL=$(wc -l < "$ALL_KEYS_FILE")
info "  scan 出 $N_TOTAL keys -> $ALL_KEYS_FILE"

> "$JFS_KEYS_FILE"
> "$EXCLUDED_FILE"
while IFS= read -r k; do
  if is_jfs_key "$k"; then
    echo "$k" >> "$JFS_KEYS_FILE"
  else
    echo "$k" >> "$EXCLUDED_FILE"
  fi
done < "$ALL_KEYS_FILE"

N_JFS=$(wc -l < "$JFS_KEYS_FILE")
N_EXC=$(wc -l < "$EXCLUDED_FILE")
info "  分类: $N_JFS 待迁 (JFS) / $N_EXC 排除 (biz)"

# 待迁分类抽样
echo
echo "  -- 待迁 (JFS) prefix 分布 --"
awk '{
  if ($0 ~ /^[a-z]+[0-9]+_/) p=substr($0,1,2)"*_*"
  else if ($0 ~ /^[a-z]+[0-9]+/) p=substr($0,1,1)"*"
  else p=$0
  cnt[p]++
}
END { for (p in cnt) printf "     %-15s %6d\n", p, cnt[p] | "sort -k2 -rn" }' "$JFS_KEYS_FILE"

# 排除清单全列(数量小)
echo
echo "  -- 排除清单 (biz keys, 不迁) --"
if (( N_EXC )); then
  while IFS= read -r k; do
    t=$(RC_BIZ type "$k" 2>/dev/null | tr -d '\r')
    sz=$(case "$t" in
      string) RC_BIZ strlen "$k" 2>/dev/null;;
      list)   RC_BIZ llen "$k" 2>/dev/null;;
      hash)   RC_BIZ hlen "$k" 2>/dev/null;;
      set)    RC_BIZ scard "$k" 2>/dev/null;;
      zset)   RC_BIZ zcard "$k" 2>/dev/null;;
      *) echo "?";;
    esac)
    printf "     %-30s %-8s size=%s\n" "$k" "$t" "$sz"
  done < "$EXCLUDED_FILE"
else
  echo "     (无)"
fi

if [[ "$MODE" == "investigate" ]]; then
  echo
  info "investigate 完成。看一眼排除清单合理不:"
  info "  - 是 alphalib biz 的 session 数据? -> 合理"
  info "  - 有看不懂的? -> 加到 06-meta-migrate.sh 的 JFS_FIXED_KEYS 或排除清单"
  info "  apply 跑: sudo -E bash $0"
  exit 0
fi

# ============================================================
# 二次确认 (apply / dry-run 都要)
# ============================================================
hr "二次确认"
echo "  待迁 keys: $N_JFS  ($JFS_KEYS_FILE)"
echo "  待排除:    $N_EXC  ($EXCLUDED_FILE)"
echo "  6379 -> 6380, batch=$BATCH, timeout=${TIMEOUT_MS}ms"
echo "  juicefs-alphalib.service 会停 ~30-60s"
[[ "$MODE" == "dry-run" ]] && echo "  (dry-run, 不真 MIGRATE)"
read -r -p "  Enter 继续 / Ctrl-C 取消: " _

# ============================================================
hr "[2/8] stop $JFS_SVC (避免 SCAN/MIGRATE 时数据在动)"
# ============================================================
WAS_ACTIVE=0
if systemctl is-active --quiet "$JFS_SVC"; then
  WAS_ACTIVE=1
  sudo systemctl stop "$JFS_SVC"
  info "  $JFS_SVC stopped"
else
  info "  $JFS_SVC 本来就没跑"
fi

# 重 scan -- 停 unit 之后, JFS keys 列表锁定
RC_BIZ --scan > "$ALL_KEYS_FILE"
> "$JFS_KEYS_FILE"
> "$EXCLUDED_FILE"
while IFS= read -r k; do
  if is_jfs_key "$k"; then
    echo "$k" >> "$JFS_KEYS_FILE"
  else
    echo "$k" >> "$EXCLUDED_FILE"
  fi
done < "$ALL_KEYS_FILE"
N_JFS=$(wc -l < "$JFS_KEYS_FILE")
N_EXC=$(wc -l < "$EXCLUDED_FILE")
info "  停 unit 后重 scan: $N_JFS 待迁 / $N_EXC 排除"

# ============================================================
hr "[3/8] 备份 6380 dump.rdb (回滚锚点, 几 KB)"
# ============================================================
TS=$(date +%Y%m%d-%H%M%S)
BAK=/var/backups/jfs-meta-migrate-$TS
sudo install -d -m 0755 -o root -g root "$BAK"
RC_JFS bgsave 2>&1 | head -3
sleep 2
sudo cp -av /var/lib/redis-jfs/dump.rdb "$BAK/dump.rdb.empty" 2>&1 | head -3 || true
sudo cp -rv /var/lib/redis-jfs/appendonlydir "$BAK/appendonlydir.empty" 2>&1 | head -3 || true

# ============================================================
hr "[4/8] MIGRATE in batches"
# ============================================================
> "$FAILED_LOG"
TOTAL=$N_JFS
DONE=0
BATCH_NUM=0

# 因为 keys 列表可能有特殊字符, 用 xargs -d '\n' 安全
mapfile -t ALL_KEYS < "$JFS_KEYS_FILE"

if [[ "$MODE" == "dry-run" ]]; then
  warn "  dry-run: 跳过真 MIGRATE"
else
  while (( DONE < TOTAL )); do
    BATCH_NUM=$((BATCH_NUM+1))
    end=$((DONE + BATCH - 1))
    (( end >= TOTAL )) && end=$((TOTAL - 1))
    batch_keys=("${ALL_KEYS[@]:$DONE:$BATCH}")
    # MIGRATE host port "" 0 timeout REPLACE AUTH password KEYS k1 k2 ...
    if RES=$(RC_BIZ MIGRATE $HOST $PORT_JFS "" 0 $TIMEOUT_MS REPLACE AUTH "$PASS_JFS" KEYS "${batch_keys[@]}" 2>&1); then
      if [[ "$RES" == "OK" || "$RES" == "NOKEY" ]]; then
        DONE=$((end + 1))
        printf "    batch %3d: %d/%d keys (%s)\r" "$BATCH_NUM" "$DONE" "$TOTAL" "$RES"
      else
        warn "    batch $BATCH_NUM 异常 RES=$RES, 写到 $FAILED_LOG"
        printf '%s\n' "${batch_keys[@]}" >> "$FAILED_LOG"
        DONE=$((end + 1))
      fi
    else
      warn "    batch $BATCH_NUM redis-cli 失败: $RES, 写到 $FAILED_LOG"
      printf '%s\n' "${batch_keys[@]}" >> "$FAILED_LOG"
      DONE=$((end + 1))
    fi
  done
  echo
  N_FAILED=$(wc -l < "$FAILED_LOG")
  info "  完成 $BATCH_NUM batches, 失败 $N_FAILED keys"
  (( N_FAILED == 0 )) || warn "  失败 keys 在 $FAILED_LOG, 可手动 MIGRATE 重试"
fi

# ============================================================
hr "[5/8] 验证 dbsize"
# ============================================================
NEW_BIZ=$(RC_BIZ dbsize)
NEW_JFS=$(RC_JFS dbsize)
info "  6379 dbsize: $TOTAL_BEFORE -> $NEW_BIZ  (期望 ~$N_EXC)"
info "  6380 dbsize: 0 -> $NEW_JFS  (期望 ~$N_JFS)"
if [[ "$MODE" != "dry-run" ]]; then
  (( NEW_JFS >= N_JFS - 5 )) || err "6380 keys 数不对, 中止. failed.txt: $FAILED_LOG"
  (( NEW_BIZ <= N_EXC + 5 )) || warn "  6379 dbsize 比预期高, 看 alphalib biz 是不是又写了 ($((NEW_BIZ - N_EXC)) 个未知)"
fi

# 验关键 setting key 在 6380
RC_JFS get setting >/dev/null 2>&1 || {
  [[ "$MODE" == "dry-run" ]] || err "6380 没有 setting key, MIGRATE 漏了"
}

# ============================================================
hr "[6/8] 改 $HOST_ENV: JFS_META_URL 指向 6380, 04-systemd 用 -jfs.env"
# ============================================================
if [[ "$MODE" == "dry-run" ]]; then
  warn "  dry-run: 跳过 conf 改写"
else
  # 移除已有的 JFS_META_URL / JFS_ENV_FILE / JFS_REDIS_UNIT (如有)
  if sudo test -f "$HOST_ENV"; then
    sudo sed -i.bak '/^JFS_META_URL=/d; /^JFS_ENV_FILE=/d; /^JFS_REDIS_UNIT=/d' "$HOST_ENV"
  else
    sudo install -m 0644 -o root -g root /dev/null "$HOST_ENV"
  fi
  sudo tee -a "$HOST_ENV" >/dev/null <<EOF
# JuiceFS metadata 已迁到独立 redis 实例 6380 (06-meta-migrate.sh on $TS)
JFS_META_URL=redis://${HOST}:${PORT_JFS}/0
JFS_ENV_FILE=${ENV_JFS}
JFS_REDIS_UNIT=${JFS_REDIS_SVC}
EOF
  info "  $HOST_ENV 已更新:"
  sudo grep -E '^(JFS_META_URL|JFS_ENV_FILE|JFS_REDIS_UNIT|JFS_MOUNT|JFS_LOCAL_DIR|JFS_CACHE_DIR)=' "$HOST_ENV" | sed 's/^/    /'
fi

# ============================================================
hr "[7/8] 重渲染 04-systemd + start $JFS_SVC"
# ============================================================
if [[ "$MODE" == "dry-run" ]]; then
  warn "  dry-run: 跳过"
else
  sudo -E bash ./04-systemd.sh >/dev/null
  sudo systemctl reset-failed "$JFS_SVC" || true
  sudo systemctl start "$JFS_SVC"
  sleep 3
  systemctl is-active --quiet "$JFS_SVC" || {
    journalctl -u "$JFS_SVC" --since '1 minute ago' --no-pager | tail -20
    err "$JFS_SVC 起不来"
  }
  info "  $JFS_SVC running"
fi

# ============================================================
hr "[8/8] mountpoint + 抽样 md5 验证"
# ============================================================
if [[ "$MODE" == "dry-run" ]]; then
  warn "  dry-run: 跳过"
else
  mountpoint "$JFS_MOUNT"
  F=$(find "$JFS_MOUNT/alpha_feature" -type f 2>/dev/null | shuf -n 1)
  if [[ -n "$F" ]]; then
    ls -la "$F"
    md5sum "$F"
  fi
fi

echo
info "DONE."
echo
info "备份: $BAK"
info "失败 keys (如有): $FAILED_LOG"
info "JFS metadata 现在在 $PORT_JFS"
info "alphalib biz 还在 $PORT_BIZ (没动)"
info ""
info "下一步:"
info "  - 跑 status.sh 全检"
info "  - client 节点 (150) 切 6380:"
info "      在 150: sudo bash /tmp/juicefs-poc/join.sh \\"
info "        --meta-host <主节点 IP> --meta-port 6380"
info "      会要新密码 (取 $ENV_JFS), 老 $ENV_BIZ 不再用"
