#!/usr/bin/env bash
# redis-persist-maxclients.sh — 把 maxclients 50000 写进 6380 真正加载的配置文件,
# 让重启 / failover 后不回落到默认 10000。在 server-160 真实终端跑:
#   sudo bash scripts/redis-persist-maxclients.sh
#
# 安全: 只改配置文件 (先备份), 不重启 redis, 不触发 sentinel failover。
#        运行中的 maxclients 之前已 CONFIG SET 到 50000, 本脚本只保证重启后一致。
set -uo pipefail
CONF=/etc/redis-jfs/redis.conf
TARGET=50000

[ "$(id -u)" -eq 0 ] || { echo "需 root: sudo bash $0" >&2; exit 1; }
[ -f "$CONF" ] || { echo "[!] 找不到 $CONF (确认 redis config_file 路径)" >&2; exit 1; }

echo "=== 改前: 配置文件里的 maxclients ==="
grep -nE '^\s*maxclients' "$CONF" || echo "(配置文件里没有显式 maxclients 行 → 当前靠默认 10000)"

ts=$(date +%Y%m%d-%H%M%S)
cp -a "$CONF" "${CONF}.bak-${ts}"
echo "[+] 已备份 -> ${CONF}.bak-${ts}"

if grep -qE '^\s*maxclients' "$CONF"; then
  sed -i -E "s/^\s*maxclients\s+.*/maxclients ${TARGET}/" "$CONF"
  echo "[+] 已替换现有 maxclients 行 -> maxclients ${TARGET}"
else
  printf '\n# bumped %s: 512-core JFS 集群, go-redis 池 ~5k/机, 10000 默认偏低\nmaxclients %s\n' "$ts" "$TARGET" >> "$CONF"
  echo "[+] 已追加 maxclients ${TARGET}"
fi

echo "=== 改后: 配置文件 ==="
grep -nE '^\s*maxclients' "$CONF"

echo "=== 校验: 运行态 vs 配置文件 (都应是 ${TARGET}) ==="
PW=$(grep -E '^META_PASSWORD=' /etc/juicefs/alphalib-jfs.env | head -1 | cut -d= -f2-)
echo -n "运行态 maxclients: "; redis-cli -p 6380 -a "$PW" --no-auth-warning CONFIG GET maxclients | tail -1
echo "[i] 不重启 redis, 故不触发 failover。下次重启会从配置文件读到 ${TARGET}。"
