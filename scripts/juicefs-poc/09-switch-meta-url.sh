#!/usr/bin/env bash
# 把本节点的 JFS_META_URL 从 直连单 redis (redis://host:6380/0) 切到 sentinel 发现
# (redis://mymaster,h1,h2,h3:26380/0)。
#
# 每节点跑一次。
#
# 用法:
#   sudo -E bash 09-switch-meta-url.sh                              # 默认 sentinel 列表
#   SENTINELS=h1,h2,h3 sudo -E bash 09-switch-meta-url.sh
#
# 前置:
#   1. 三个 sentinel 都已经起来(08-sentinel.sh 在 160/150/144 上跑过)
#   2. master + replica 都已起 + replication OK
#
# 步骤:
#   [1] 验证 sentinel 集群健康 (互查能看到对方)
#   [2] 改 /etc/juicefs-poc.env 的 JFS_META_URL
#   [3] 重跑 04-systemd.sh (重渲染 unit,新 URL 进 ExecStart)
#   [4] systemctl restart juicefs-<name>.service
#   [5] 验证挂载点还活着 + 能读写
#
# 幂等。

set -euo pipefail
cd "$(dirname "$0")"
source ./_lib.sh
source ./config.sh

require_sudo
require_systemd
require_bin redis-cli

# ============================================================
# 配置
# ============================================================
SENTINELS_DEFAULT="10.9.100.160,10.9.100.150,10.6.100.144"
SENTINELS="${SENTINELS:-$SENTINELS_DEFAULT}"
SENTINEL_PORT="${SENTINEL_PORT:-26380}"
MASTER_NAME="${MASTER_NAME:-mymaster}"
DB="${DB:-0}"
POC_ENV="${POC_ENV:-/etc/juicefs-poc.env}"
SVC="juicefs-${JFS_NAME}.service"

NEW_META_URL="redis://${MASTER_NAME},${SENTINELS}:${SENTINEL_PORT}/${DB}"

# ============================================================
# [1] 健康检查
# ============================================================
info "[1/5] sentinel 集群健康检查"
SEEN_OK=0
IFS=',' read -ra SARR <<< "$SENTINELS"
for s in "${SARR[@]}"; do
  if ! timeout 3 bash -c "echo > /dev/tcp/$s/$SENTINEL_PORT" 2>/dev/null; then
    warn "  $s:$SENTINEL_PORT TCP 不通"
    continue
  fi
  # 询问该 sentinel 它认识哪个 master
  IP=$(redis-cli -h "$s" -p "$SENTINEL_PORT" sentinel get-master-addr-by-name "$MASTER_NAME" 2>/dev/null | head -1)
  PORT=$(redis-cli -h "$s" -p "$SENTINEL_PORT" sentinel get-master-addr-by-name "$MASTER_NAME" 2>/dev/null | tail -1)
  if [[ -z "$IP" || "$IP" == "(nil)" ]]; then
    warn "  $s:$SENTINEL_PORT 看不到 $MASTER_NAME (sentinel 没起好?)"
    continue
  fi
  info "  $s:$SENTINEL_PORT → master=$IP:$PORT"
  SEEN_OK=$((SEEN_OK + 1))
done

if (( SEEN_OK < 2 )); then
  err "至少要 2 个 sentinel 健康才能切 (当前 $SEEN_OK/${#SARR[@]})"
fi

# ============================================================
# [2] 改 POC env
# ============================================================
info "[2/5] 更新 $POC_ENV"
if ! sudo test -f "$POC_ENV"; then
  err "$POC_ENV 不存在 (运行 join.sh 或 06-meta-migrate.sh 之后才有)"
fi

# 看现状
CUR=$(sudo grep -E '^JFS_META_URL=' "$POC_ENV" | head -1 | cut -d= -f2-)
info "  当前: $CUR"
info "  目标: $NEW_META_URL"

if [[ "$CUR" == "$NEW_META_URL" ]]; then
  info "  已是目标值,跳过 [2]"
else
  sudo sed -i.bak.$(date +%s) -E "s|^JFS_META_URL=.*|JFS_META_URL=$NEW_META_URL|" "$POC_ENV"
  info "  $POC_ENV 已更新 (备份 .bak.<ts>)"
fi

# ============================================================
# [3] 重新渲染 systemd unit
# ============================================================
info "[3/5] 重渲染 $SVC unit (重新 source $POC_ENV)"
sudo -E bash ./04-systemd.sh >/dev/null

# ============================================================
# [4] restart juicefs unit
# ============================================================
info "[4/5] systemctl restart $SVC"

# writeback drain 警告
sb=$(awk '$1=="juicefs_staging_blocks"{print $2; exit}' "${JFS_MOUNT}/.stats" 2>/dev/null || echo 0)
sw=$(awk '$1=="juicefs_staging_writing_blocks"{print $2; exit}' "${JFS_MOUNT}/.stats" 2>/dev/null || echo 0)
if [[ "${sb:-0}" != "0" || "${sw:-0}" != "0" ]]; then
  warn "  writeback 有未刷完: staging=$sb writing=$sw"
  warn "  systemd unit ExecStop 会 juicefs umount 强 drain;先确认能等"
fi

sudo systemctl restart "$SVC"
sleep 5
systemctl is-active --quiet "$SVC" || {
  journalctl -u "$SVC" --no-pager -n 40
  err "$SVC restart 失败,检查 URL 格式 + sentinel 可达性"
}
info "  $SVC active"

# ============================================================
# [5] 挂载点验证
# ============================================================
info "[5/5] mount 验证"
mountpoint -q "$JFS_MOUNT" || err "$JFS_MOUNT 没挂上"
info "  $JFS_MOUNT mounted"

# 读写 smoke (touch + cat + rm,~/.tmp 测)
TMPF="$JFS_MOUNT/.switch-test-$(hostname -s)-$$"
echo "switch-ok $(date -Iseconds)" | sudo tee "$TMPF" >/dev/null
sudo cat "$TMPF" | head -1 | sed 's/^/    /'
sudo rm -f "$TMPF"
info "  rw smoke OK"

echo
info "DONE."
info "  $POC_ENV.JFS_META_URL = $NEW_META_URL"
info "  $SVC unit 已重渲染 + restart"
echo
info "  下一步:在三节点都跑完后,改 config.juicefs.yaml 的 state.redis.url"
info "  到 'redis-sentinel://${SENTINELS//,/:$SENTINEL_PORT,}:${SENTINEL_PORT}/${MASTER_NAME}/${DB}'"
info "  (ops 自己的 sentinel scheme,跟 JFS 格式不同;详见 ops/infra/store/redis_store.py)"
