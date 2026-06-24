#!/usr/bin/env bash
# redis-bump-maxclients.sh — 在 server-160 上跑。
#
# 背景: 6380 是 JuiceFS metadata + ops state 共生实例。连接打满 (默认 maxclients
# 10000) 后, 新连接连 AUTH 都被拒 (max number of clients reached), ops / redis-cli
# 全部连不进去。本脚本循环重试挤进一个连接空隙, 把 maxclients 调大并写回配置。
#
# 用法 (在 160 上, 需要 sudo 读 password_file):
#   sudo bash scripts/redis-bump-maxclients.sh
#   sudo bash scripts/redis-bump-maxclients.sh 80000      # 自定目标值, 默认 50000
#
# 注意: maxclients 受 redis 进程 ulimit -n 约束 (需 >= maxclients+32)。脚本最后会
# 打印实际生效值, 若没到目标值就是被 systemd LimitNOFILE 卡了, 要先改 unit。
set -uo pipefail

TARGET="${1:-50000}"
PORT=6380
PWFILE=/etc/juicefs/alphalib-jfs.env
PWKEY=META_PASSWORD
RETRIES=600          # 600 * 0.3s ≈ 3 分钟内不断重试挤连接空隙
SLEEP=0.3

PW=$(grep -E "^${PWKEY}=" "$PWFILE" | head -1 | cut -d= -f2-)
if [ -z "$PW" ]; then
  echo "[!] 读不到密码 ($PWFILE 的 $PWKEY)。是否用 sudo 跑?" >&2
  exit 1
fi

rcli() { redis-cli -p "$PORT" -a "$PW" --no-auth-warning "$@"; }

echo "[i] 目标 maxclients=$TARGET, 循环重试挤连接空隙 (最多 ${RETRIES} 次)..."
ok=0
for i in $(seq 1 "$RETRIES"); do
  if out=$(rcli CONFIG SET maxclients "$TARGET" 2>&1) && [ "$out" = "OK" ]; then
    echo "[+] 第 $i 次重试挤进, CONFIG SET 成功"
    ok=1
    break
  fi
  sleep "$SLEEP"
done

if [ "$ok" -ne 1 ]; then
  echo "[!] ${RETRIES} 次都没挤进 — 池子已死贴上限。" >&2
  echo "    先腾空隙再跑本脚本:" >&2
  echo "      ssh 10.9.100.150 'pkill -STOP -u rui -f alpha_lib.cli'   # 暂停 rui autoalpha (知会一声)" >&2
  echo "    调完恢复:" >&2
  echo "      ssh 10.9.100.150 'pkill -CONT -u rui -f alpha_lib.cli'" >&2
  exit 2
fi

echo "[i] CONFIG REWRITE (写回配置文件, 重启不丢)..."
rcli CONFIG REWRITE && echo "[+] REWRITE OK" || echo "[!] REWRITE 失败 (可能 redis 无 configfile, 重启会丢, 需手改 /etc/redis/*.conf)"

echo "[i] 当前生效值 / 实时连接数:"
rcli CONFIG GET maxclients
echo -n "connected_clients: "; rcli INFO clients 2>/dev/null | grep -E '^connected_clients:' | tr -d '\r'
