#!/usr/bin/env bash
# Redis 故障注入测试。
#
# 场景:在持续读写过程中 kill Redis,观察 JuiceFS 行为;然后重启 Redis,
# 看 IO 是否自动恢复 + 数据是否完整。
#
# 决定生产是否需要 Redis Sentinel 或 TiKV。
#
# 需要 sudo(systemctl restart redis-server)。

set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh

T="$JFS_MOUNT/_poc_redis"
rm -rf "$T" && mkdir -p "$T"

LOG="$T/writer.log"
RESULTS="$T/results.txt"

# 后台 writer:每秒写一行,记录是否成功 + 耗时
writer() {
  local i=0
  while true; do
    i=$((i + 1))
    local t0=$(date +%s.%N)
    if echo "$i $(date +%T.%3N) hello" > "$T/w_$i.txt" 2>>"$LOG"; then
      local t1=$(date +%s.%N)
      local ms=$(awk -v a=$t1 -v b=$t0 'BEGIN{printf "%.0f", (a-b)*1000}')
      echo "$(date +%T.%3N) write #$i OK ${ms}ms" >> "$RESULTS"
    else
      echo "$(date +%T.%3N) write #$i FAIL" >> "$RESULTS"
    fi
    sleep 1
  done
}

echo "=========================================================="
echo "Redis failure injection test"
echo "=========================================================="

echo "[1/6] starting background writer (1 write/sec)..."
writer &
WRITER_PID=$!
trap 'kill $WRITER_PID 2>/dev/null; rm -rf "$T"' EXIT

sleep 3
echo "  writer baseline (前 3 秒,Redis 健在):"
tail -3 "$RESULTS" 2>/dev/null | sed 's/^/    /'

echo
echo "[2/6] killing redis-server..."
T_KILL=$(date +%T.%3N)
sudo systemctl stop redis-server
echo "  killed at $T_KILL"

echo
echo "[3/6] writer 在 Redis 死亡期间的状态(等 8 秒)..."
sleep 8
echo "  最近 8 秒的 writer 日志:"
tail -10 "$RESULTS" 2>/dev/null | sed 's/^/    /'

echo
echo "[4/6] restarting redis-server..."
T_REST=$(date +%T.%3N)
sudo systemctl start redis-server
sleep 1
redis-cli ping
echo "  restarted at $T_REST"

echo
echo "[5/6] 等 8 秒看 writer 是否自动恢复..."
sleep 8
echo "  Redis 恢复后的 writer 日志:"
tail -10 "$RESULTS" 2>/dev/null | sed 's/^/    /'

kill $WRITER_PID 2>/dev/null || true
wait $WRITER_PID 2>/dev/null || true

echo
echo "[6/6] 数据完整性检查..."
TOTAL_OK=$(grep -c " OK " "$RESULTS" 2>/dev/null || echo 0)
TOTAL_FAIL=$(grep -c " FAIL$" "$RESULTS" 2>/dev/null || echo 0)
TOTAL_FILES=$(ls "$T"/w_*.txt 2>/dev/null | wc -l)
echo "  日志记录: ${TOTAL_OK} 次成功,${TOTAL_FAIL} 次失败"
echo "  实际文件: ${TOTAL_FILES} 个"
if [ "$TOTAL_OK" = "$TOTAL_FILES" ]; then
  echo "  ✅ 文件数与成功次数一致,无丢失"
else
  echo "  ⚠️  不一致,差异 = $((TOTAL_OK - TOTAL_FILES))"
fi

# 抽样验证内容
echo "  抽样内容:"
ls "$T"/w_*.txt 2>/dev/null | head -3 | while read f; do
  echo "    $(basename $f): $(cat $f)"
done
ls "$T"/w_*.txt 2>/dev/null | tail -3 | while read f; do
  echo "    $(basename $f): $(cat $f)"
done

echo
echo "=========================================================="
echo "DONE. 完整日志: $RESULTS"
echo "(test dir 会在脚本退出时清理)"
echo "=========================================================="