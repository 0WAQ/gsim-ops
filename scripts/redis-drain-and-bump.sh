#!/usr/bin/env bash
# redis-drain-and-bump.sh — 在 server-160 上跑 (redis master + 你现在这台)。
#
# 现象: 6380 连接打满 (12000+ > maxclients 10000), 且连接 30s 完全静止 =
#       juicefs mount 的 metadata 连接池泄漏 (只增不收, 空闲不释放)。
#       因为没有连接断开, "挤空隙调大 maxclients" 永远失败。
#
# 本脚本: 从内核层强制关闭 150 -> 160:6380 那群泄漏 socket (最大头, 已静止≈空闲),
#         瞬间腾出 ~7000 slot, 然后立刻调大 maxclients 并写回配置。
#
# 安全性:
#   - 只踢 dst=150 的 socket, 不动 160 本机 / 145。
#   - juicefs 为 metadata 断连设计, 会自动重连 (用全新小连接池)。
#   - --writeback 缓存在本地 cache-dir 磁盘上, 不在这些 socket 里, 不丢数据。
#   - blast radius: 仅 150 的 JFS metadata 瞬断重连 (亚秒级), 在跑的读写重试一下。
#
# 用法 (在 160 真实终端, 需要 tty 给 sudo 弹密码):
#   sudo bash scripts/redis-drain-and-bump.sh
#   sudo bash scripts/redis-drain-and-bump.sh 80000   # 自定 maxclients, 默认 50000
set -uo pipefail

TARGET="${1:-50000}"
PORT=6380
LEAK_PEER=10.9.100.150          # 泄漏最严重的客户端 (150 的 juicefs mount)
PWFILE=/etc/juicefs/alphalib-jfs.env
PWKEY=META_PASSWORD

if [ "$(id -u)" -ne 0 ]; then
  echo "[!] 需要 root。用: sudo bash $0" >&2
  exit 1
fi

echo "=== 0. 踢之前的连接快照 ==="
before=$(ss -tn state established '( sport = :'"$PORT"' )' 2>/dev/null | tail -n +2 | wc -l)
echo "    6380 server 侧 ESTABLISHED 总数: $before"
echo "    其中 dst=$LEAK_PEER: $(ss -tn state established dst "$LEAK_PEER":'*' 2>/dev/null | tail -n +2 | grep -c ":$PORT" || true)"

echo "=== 1. 内核层强制关闭 150 -> 6380 的泄漏 socket ==="
# ss -K 需要内核 CONFIG_INET_DIAG_DESTROY。先匹配 sport=6380 且 peer=150。
if ss -K state established '( sport = :'"$PORT"' )' dst "$LEAK_PEER" 2>/dev/null; then
  echo "    [+] ss -K 已执行"
else
  rc=$?
  echo "    [!] ss -K 失败 (rc=$rc) — 内核可能没开 INET_DIAG_DESTROY。" >&2
  echo "        退路: 在 150 上重启 juicefs mount 释放连接 (见脚本尾注)。" >&2
  exit 2
fi
sleep 1
after=$(ss -tn state established '( sport = :'"$PORT"' )' 2>/dev/null | tail -n +2 | wc -l)
echo "    踢后 6380 server 侧 ESTABLISHED 总数: $after  (释放 $((before-after)) 条)"

echo "=== 2. 调大 maxclients (现在有空 slot 了) ==="
PW=$(grep -E "^${PWKEY}=" "$PWFILE" | head -1 | cut -d= -f2-)
if [ -z "$PW" ]; then echo "[!] 读不到 redis 密码 ($PWFILE)" >&2; exit 3; fi
rcli() { redis-cli -p "$PORT" -a "$PW" --no-auth-warning "$@"; }

ok=0
for i in $(seq 1 50); do
  if [ "$(rcli CONFIG SET maxclients "$TARGET" 2>/dev/null)" = "OK" ]; then
    echo "    [+] CONFIG SET maxclients $TARGET OK (第 $i 次)"; ok=1; break
  fi
  sleep 0.2
done
[ "$ok" -ne 1 ] && { echo "[!] 仍连不进, 释放的 slot 可能又被重连占满 — 见尾注重启 juicefs" >&2; exit 4; }

echo "=== 3. 写回配置 (重启不丢) ==="
# 注意: redis-cli 碰到 redis 返回的 -ERR 时退出码仍是 0, 不能用 && 判断成功。
# 必须看输出内容里有没有 error。
rw_out=$(rcli CONFIG REWRITE 2>&1)
case "$rw_out" in
  OK) echo "    [+] REWRITE OK" ;;
  *) echo "    [!] REWRITE 失败: $rw_out"
     echo "        (常见: redis 对 config 目录无写权)。改持久化到配置文件:"
     echo "        sudo bash scripts/redis-persist-maxclients.sh" ;;
esac

echo "=== 4. 确认 ==="
rcli CONFIG GET maxclients
echo -n "    connected_clients: "; rcli INFO clients 2>/dev/null | grep -E '^connected_clients:' | tr -d '\r'

# 尾注 — 若 ss -K 不可用, 在 150 上释放连接的退路 (graceful, 不丢 writeback):
#   ssh 10.9.100.150 'sudo systemctl restart juicefs-alphalib'   # 若有 systemd unit
#   或: sudo juicefs umount /tank/vault/alphalib && 重新 mount   # 手动 graceful
