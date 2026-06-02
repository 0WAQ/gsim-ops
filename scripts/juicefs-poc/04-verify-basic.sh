#!/usr/bin/env bash
# 基础读写 / flock / stat 性能测试。
# 所有产物都在 $JFS_MOUNT/_poc_basic/ 下,跑完会清理。

set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh

T="$JFS_MOUNT/_poc_basic"
rm -rf "$T" && mkdir -p "$T" && cd "$T"

echo "[1/5] small file write + read..."
echo "hello juicefs" > f.txt
[[ "$(cat f.txt)" == "hello juicefs" ]] && echo "  ok" || { echo "  FAIL"; exit 1; }

echo "[2/5] 100 MB file write + read (測本地 cache + S3 上传)..."
t=$(date +%s.%N)
dd if=/dev/urandom of=big.bin bs=1M count=100 status=none
t2=$(date +%s.%N)
WRITE_MS=$(awk -v a=$t2 -v b=$t 'BEGIN{printf "%.0f", (a-b)*1000}')
SIZE=$(stat -c%s big.bin)
echo "  wrote 100 MB in ${WRITE_MS} ms, size=$SIZE"

t=$(date +%s.%N)
md5sum big.bin > /dev/null
t2=$(date +%s.%N)
READ_MS=$(awk -v a=$t2 -v b=$t 'BEGIN{printf "%.0f", (a-b)*1000}')
echo "  re-read+md5 in ${READ_MS} ms (cache 命中应该 <500ms)"

echo "[3/5] flock 串行化(同机两进程)..."
(
  flock -x 200
  echo "  A acquired @ $(date +%T.%3N)"
  sleep 1
  echo "  A releasing @ $(date +%T.%3N)"
) 200>l.lock &
sleep 0.2
(
  flock -x 200
  echo "  B acquired @ $(date +%T.%3N) (应该在 A 释放后)"
) 200>l.lock
wait
echo "  ok"

echo "[4/5] stat 1000 个小文件(测 metadata 性能)..."
mkdir -p stat_test && cd stat_test
for i in $(seq 1 1000); do echo $i > f$i.txt; done
t=$(date +%s.%N)
ls -la > /dev/null
t2=$(date +%s.%N)
STAT_MS=$(awk -v a=$t2 -v b=$t 'BEGIN{printf "%.0f", (a-b)*1000}')
echo "  ls -la 1000 files: ${STAT_MS} ms (Redis 本地应该 <500ms)"
cd ..

echo "[5/5] 跨进程可见性(写者退出后,读者立刻可见)..."
(echo "from-A" > visible.txt) && [[ "$(cat visible.txt)" == "from-A" ]] && echo "  ok"

echo
echo "cleaning up $T..."
cd "$JFS_MOUNT" && rm -rf "$T"

echo
echo "DONE. Next: ./05-verify-memmap.sh"
