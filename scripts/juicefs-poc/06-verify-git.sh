#!/usr/bin/env bash
# Git on JuiceFS 性能测试。
#
# 模拟 500 个因子提交(每个一次 commit),然后测查询类操作的延迟:
#   git log / git log -- file / git blame / git status / git diff
#
# 同时在本地 ZFS 上跑一份做基线对比,看 FUSE 开销究竟多大。
#
# 跑完不清理,可以手动 ls 看下 .git/ 大小。
# 重跑前删除测试目录: rm -rf $JFS_MOUNT/_poc_git /tank/vault/_poc_git_local

set -euo pipefail
cd "$(dirname "$0")"
source ./00-config.sh

N_FACTORS=${N_FACTORS:-500}

# 两个测试位置
JFS_REPO="$JFS_MOUNT/_poc_git"
LOCAL_REPO="/tank/vault/_poc_git_local"

# 清理并重建
echo "== 清理旧测试目录 =="
rm -rf "$JFS_REPO" "$LOCAL_REPO"
mkdir -p "$JFS_REPO" "$LOCAL_REPO"

# 生成一个假因子目录(三个文件)
make_factor() {
  local repo="$1"
  local name="$2"
  local idx="$3"
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
<Backtest>
  <Data>table_$idx</Data>
  <Param>v=$idx</Param>
</Backtest>
EOF
  cat > "$repo/$name/Readme.$name.md" <<EOF
# $name
factor index: $idx
auto-generated test factor
EOF
}

# ----- 单次试验 -----
benchmark_one() {
  local repo="$1"
  local label="$2"

  cd "$repo"
  git init -q
  git config user.email "poc@test"
  git config user.name "poc"
  git config commit.gpgsign false

  echo
  echo "--- $label: 顺序提交 $N_FACTORS 因子 ---"
  local t0=$(date +%s.%N)
  for i in $(seq 1 $N_FACTORS); do
    local name=$(printf "AlphaTest%04d" $i)
    make_factor "$repo" "$name" "$i"
    git add "$name" >/dev/null
    git commit -q -m "submit $name"
  done
  local t1=$(date +%s.%N)
  local total_ms=$(awk -v a=$t1 -v b=$t0 'BEGIN{printf "%.0f", (a-b)*1000}')
  local per_commit_ms=$(awk -v t=$total_ms -v n=$N_FACTORS 'BEGIN{printf "%.1f", t/n}')
  echo "  总耗时: ${total_ms} ms,平均每 commit: ${per_commit_ms} ms"

  echo
  echo "--- $label: 查询类操作延迟 ---"
  for q in \
    "git log --oneline" \
    "git log --oneline AlphaTest0001/AlphaTest0001.py" \
    "git blame AlphaTest0001/AlphaTest0001.py" \
    "git status" \
    "git diff HEAD~10 HEAD --stat" \
    ; do
    local t=$(date +%s.%N)
    eval "$q" >/dev/null 2>&1 || true
    local t2=$(date +%s.%N)
    local ms=$(awk -v a=$t2 -v b=$t 'BEGIN{printf "%.0f", (a-b)*1000}')
    printf "  %-50s  %s ms\n" "$q" "$ms"
  done

  echo
  echo "--- $label: 仓库体积 ---"
  du -sh "$repo/.git" 2>/dev/null
  echo "  objects: $(find "$repo/.git/objects" -type f 2>/dev/null | wc -l)"

  cd - >/dev/null
}

echo "========================================================="
echo "Git on JuiceFS PoC: $N_FACTORS 个假因子提交 + 查询基线"
echo "========================================================="
benchmark_one "$JFS_REPO"   "JuiceFS (FUSE)"
benchmark_one "$LOCAL_REPO" "Local ZFS (基线)"

echo
echo "========================================================="
echo "DONE. 手动清理: rm -rf $JFS_REPO $LOCAL_REPO"
echo "========================================================="