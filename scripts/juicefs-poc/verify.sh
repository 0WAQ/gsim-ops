#!/usr/bin/env bash
# JuiceFS PoC 验证套件。
#
# 用法:
#   bash verify.sh                # 跑全部 (basic + memmap + git + redis-fail)
#   bash verify.sh basic          # 100MB IO / flock / stat / 可见性
#   bash verify.sh memmap         # alpha_feature 模式仿真
#   bash verify.sh git            # 500 commit + log/blame/status/diff
#   bash verify.sh redis-fail     # Redis kill 注入 (需 sudo)
#
# 所有产物都在 $JFS_MOUNT/_poc_*/ 下,跑完会清理。

set -euo pipefail
cd "$(dirname "$0")"
source ./_lib.sh
source ./config.sh

require_mountpoint "$JFS_MOUNT"

# ============================================================
# basic
# ============================================================
verify_basic() {
  info "== basic: write/read/flock/stat/可见性 =="
  local T="$JFS_MOUNT/_poc_basic"
  rm -rf "$T" && mkdir -p "$T" && cd "$T"

  echo "[1/5] 小文件写读"
  echo "hello juicefs" > f.txt
  [[ "$(cat f.txt)" == "hello juicefs" ]] && echo "  ok" || err "FAIL"

  echo "[2/5] 100 MB 写读"
  local t t2 WRITE_MS READ_MS SIZE
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
  echo "  re-read+md5 in ${READ_MS} ms"

  echo "[3/5] flock 同机两进程串行化"
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

  echo "[4/5] stat 1000 个小文件"
  mkdir -p stat_test && cd stat_test
  for i in $(seq 1 1000); do echo $i > f$i.txt; done
  t=$(date +%s.%N)
  ls -la > /dev/null
  t2=$(date +%s.%N)
  local STAT_MS; STAT_MS=$(awk -v a=$t2 -v b=$t 'BEGIN{printf "%.0f", (a-b)*1000}')
  echo "  ls -la 1000 files: ${STAT_MS} ms"
  cd ..

  echo "[5/5] 跨进程可见性"
  (echo "from-A" > visible.txt) && [[ "$(cat visible.txt)" == "from-A" ]] && echo "  ok"

  cd "$JFS_MOUNT" && rm -rf "$T"
}

# ============================================================
# memmap
# ============================================================
verify_memmap() {
  info "== memmap: alpha_feature 仿真 =="
  local OPS_ROOT="${OPS_ROOT:-/home/wbai/gsim-ops}"
  [[ -d "$OPS_ROOT" ]] || err "OPS_ROOT=$OPS_ROOT 不存在"
  uv run --project "$OPS_ROOT" python "$(pwd)/verify_memmap.py"
}

# ============================================================
# git
# ============================================================
verify_git() {
  info "== git: 500 commit + 查询延迟 =="
  local N_FACTORS="${N_FACTORS:-500}"
  local JFS_REPO="$JFS_MOUNT/_poc_git"
  local LOCAL_REPO="/tank/vault/_poc_git_local"

  rm -rf "$JFS_REPO" "$LOCAL_REPO"
  mkdir -p "$JFS_REPO" "$LOCAL_REPO"

  _make_factor() {
    local repo=$1 name=$2 idx=$3
    mkdir -p "$repo/$name"
    cat > "$repo/$name/$name.py" <<EOF
from gsim import AlphaBase, DataRegistry as dr
import numpy as np

class $name(AlphaBase):
    def __init__(self, cfg):
        super().__init__(cfg)
        self.x = dr.getData('table_$idx.col_a')

    def generate(self, di):
        self.alpha[self.valid[di]] = self.x[di, self.valid[di]] * $idx
EOF
    cat > "$repo/$name/Config.$name.xml" <<EOF
<Backtest><Data>table_$idx</Data><Param>v=$idx</Param></Backtest>
EOF
    echo "# $name (idx=$idx)" > "$repo/$name/Readme.$name.md"
  }

  _bench_one() {
    local repo=$1 label=$2
    cd "$repo"
    git init -q
    git config user.email "poc@test"
    git config user.name "poc"
    git config commit.gpgsign false

    echo
    echo "--- $label: 顺序提交 $N_FACTORS 因子 ---"
    local t0 t1 total_ms per_commit_ms
    t0=$(date +%s.%N)
    for i in $(seq 1 $N_FACTORS); do
      local name; name=$(printf "AlphaTest%04d" $i)
      _make_factor "$repo" "$name" "$i"
      git add "$name" >/dev/null
      git commit -q -m "submit $name"
    done
    t1=$(date +%s.%N)
    total_ms=$(awk -v a=$t1 -v b=$t0 'BEGIN{printf "%.0f", (a-b)*1000}')
    per_commit_ms=$(awk -v t=$total_ms -v n=$N_FACTORS 'BEGIN{printf "%.1f", t/n}')
    echo "  总耗时: ${total_ms} ms, 平均 ${per_commit_ms} ms/commit"

    echo
    echo "--- $label: 查询类操作 ---"
    local q t t2 ms
    for q in \
      "git log --oneline" \
      "git log --oneline AlphaTest0001/AlphaTest0001.py" \
      "git blame AlphaTest0001/AlphaTest0001.py" \
      "git status" \
      "git diff HEAD~10 HEAD --stat" \
      ; do
      t=$(date +%s.%N)
      eval "$q" >/dev/null 2>&1 || true
      t2=$(date +%s.%N)
      ms=$(awk -v a=$t2 -v b=$t 'BEGIN{printf "%.0f", (a-b)*1000}')
      printf "  %-50s  %s ms\n" "$q" "$ms"
    done

    echo
    echo "--- $label: 仓库体积 ---"
    du -sh "$repo/.git" 2>/dev/null
    echo "  objects: $(find "$repo/.git/objects" -type f 2>/dev/null | wc -l)"
    cd - >/dev/null
  }

  _bench_one "$JFS_REPO"   "JuiceFS (FUSE)"
  _bench_one "$LOCAL_REPO" "Local ZFS (基线)"
  warn "  手动清理: rm -rf $JFS_REPO $LOCAL_REPO"
}

# ============================================================
# redis-fail
# ============================================================
verify_redis_fail() {
  info "== redis-fail: kill + restart =="
  require_sudo
  local T="$JFS_MOUNT/_poc_redis"
  rm -rf "$T" && mkdir -p "$T"
  local LOG="$T/writer.log" RESULTS="$T/results.txt"

  _writer() {
    local i=0
    while true; do
      i=$((i + 1))
      local t0; t0=$(date +%s.%N)
      if echo "$i $(date +%T.%3N) hello" > "$T/w_$i.txt" 2>>"$LOG"; then
        local t1; t1=$(date +%s.%N)
        local ms; ms=$(awk -v a=$t1 -v b=$t0 'BEGIN{printf "%.0f", (a-b)*1000}')
        echo "$(date +%T.%3N) write #$i OK ${ms}ms" >> "$RESULTS"
      else
        echo "$(date +%T.%3N) write #$i FAIL" >> "$RESULTS"
      fi
      sleep 1
    done
  }

  echo "[1/6] 起 background writer"
  _writer &
  local WRITER_PID=$!
  trap 'kill $WRITER_PID 2>/dev/null; rm -rf "$T"' RETURN

  sleep 3
  echo "  baseline:"; tail -3 "$RESULTS" | sed 's/^/    /'

  echo
  echo "[2/6] kill redis-server @ $(date +%T.%3N)"
  sudo systemctl stop redis-server

  echo
  echo "[3/6] 等 8 秒看 writer"
  sleep 8
  tail -10 "$RESULTS" | sed 's/^/    /'

  echo
  echo "[4/6] restart redis-server @ $(date +%T.%3N)"
  sudo systemctl start redis-server
  sleep 1
  # 如果有密码,要 source env 才能 ping
  if [[ -f /etc/juicefs/${JFS_NAME}.env ]]; then
    sudo bash -c ". /etc/juicefs/${JFS_NAME}.env && redis-cli -a \$META_PASSWORD ping" 2>/dev/null | grep -q PONG \
      && echo "  PONG" || warn "  PONG 失败"
  else
    redis-cli ping
  fi

  echo
  echo "[5/6] 等 8 秒看 writer 恢复"
  sleep 8
  tail -10 "$RESULTS" | sed 's/^/    /'

  kill $WRITER_PID 2>/dev/null || true
  wait $WRITER_PID 2>/dev/null || true

  echo
  echo "[6/6] 数据完整性"
  local TOTAL_OK TOTAL_FAIL TOTAL_FILES
  TOTAL_OK=$(grep -c " OK " "$RESULTS" 2>/dev/null || echo 0)
  TOTAL_FAIL=$(grep -c " FAIL$" "$RESULTS" 2>/dev/null || echo 0)
  TOTAL_FILES=$(ls "$T"/w_*.txt 2>/dev/null | wc -l)
  echo "  日志: ${TOTAL_OK} OK / ${TOTAL_FAIL} FAIL,实际文件 ${TOTAL_FILES}"
  if [[ "$TOTAL_OK" == "$TOTAL_FILES" ]]; then
    info "  无丢失"
  else
    warn "  差异 $((TOTAL_OK - TOTAL_FILES))"
  fi
}

# ============================================================
# main
# ============================================================
ALL=(basic memmap git redis-fail)

usage() {
  echo "用法: bash verify.sh [${ALL[*]} | all]"
  exit 1
}

if [[ $# -eq 0 ]] || [[ "${1:-}" == "all" ]]; then
  TARGETS=("${ALL[@]}")
elif [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
else
  TARGETS=("$@")
fi

for t in "${TARGETS[@]}"; do
  case "$t" in
    basic)      verify_basic ;;
    memmap)     verify_memmap ;;
    git)        verify_git ;;
    redis-fail) verify_redis_fail ;;
    *) err "未知: $t (可选: ${ALL[*]})" ;;
  esac
  echo
done

info "DONE."
