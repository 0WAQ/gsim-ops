#!/usr/bin/env bash
# JuiceFS PoC 一把梭健康检查。本机视角,exit 1 if 任何 ✗。
#
# 用法:
#   bash status.sh        # 简版,不动 redis
#   sudo bash status.sh   # 全套 (能读 /etc/juicefs/*.env, 测 AUTH)
#
# 全 read-only,可以随时跑。

set -uo pipefail
cd "$(dirname "$0")"
source ./_lib.sh
source ./config.sh

CHECKS=0; FAILS=0
pass() { CHECKS=$((CHECKS+1)); _grn "  ✓ $*"; }
fail() { CHECKS=$((CHECKS+1)); FAILS=$((FAILS+1)); _red "  ✗ $*"; }
note() { printf '    %s\n' "$*"; }

SVC="juicefs-${JFS_NAME}.service"
ENV_FILE="/etc/juicefs/${JFS_NAME}.env"
HAS_SUDO=0
sudo -n true 2>/dev/null && HAS_SUDO=1

# ============================================================
echo
info "=== Mount ==="
# ============================================================
if mountpoint -q "$JFS_MOUNT"; then
  src=$(mount | awk -v m="$JFS_MOUNT" '$3==m {print $1; exit}')
  pass "$JFS_MOUNT mounted ($src)"
  if systemctl is-active --quiet "$SVC" 2>/dev/null; then
    pass "$SVC active"
  else
    note "$SVC 未通过 systemd 管理 (手动 mount?)"
  fi
else
  fail "$JFS_MOUNT 没挂载"
fi

# ============================================================
echo
info "=== Cache ==="
# ============================================================
if [[ -d "$JFS_CACHE_DIR" ]]; then
  free_b=$(df -B1 --output=avail "$JFS_CACHE_DIR" | tail -1)
  used_b=""
  if (( HAS_SUDO )); then
    used_b=$(sudo du -sb "$JFS_CACHE_DIR" 2>/dev/null | awk '{print $1}')
  fi
  if [[ -n "$used_b" ]]; then
    pass "cache: used $(numfmt --to=iec $used_b), free $(numfmt --to=iec $free_b), cap ${JFS_CACHE_SIZE_MB} MB"
  else
    pass "cache dir 存在 ($JFS_CACHE_DIR), free $(numfmt --to=iec $free_b) (用 sudo 看 used)"
  fi
else
  fail "cache dir 缺: $JFS_CACHE_DIR"
fi

# ============================================================
echo
info "=== Redis ==="
# ============================================================
META_HOST=$(echo "$JFS_META_URL" | sed -E 's|redis://([^:/]+):.*|\1|')
META_PORT=$(echo "$JFS_META_URL" | sed -E 's|redis://[^:]+:([0-9]+).*|\1|')

if timeout 3 bash -c "echo > /dev/tcp/$META_HOST/$META_PORT" 2>/dev/null; then
  pass "TCP $META_HOST:$META_PORT 可达"
  if (( HAS_SUDO )) && sudo test -r "$ENV_FILE"; then
    if sudo bash -c ". $ENV_FILE && redis-cli -h $META_HOST -p $META_PORT -a \"\$META_PASSWORD\" ping 2>/dev/null" | grep -q PONG; then
      pass "AUTH PONG"
      aof=$(sudo bash -c ". $ENV_FILE && redis-cli -h $META_HOST -p $META_PORT -a \"\$META_PASSWORD\" config get appendonly 2>/dev/null" | tail -1 | tr -d '\r')
      if [[ "$aof" == "yes" ]]; then
        pass "AOF on"
      else
        fail "AOF off (重启可能丢 metadata,跑 03-redis.sh 开)"
      fi
      mem=$(sudo bash -c ". $ENV_FILE && redis-cli -h $META_HOST -p $META_PORT -a \"\$META_PASSWORD\" info memory 2>/dev/null" | grep '^used_memory_human:' | tr -d '\r' | cut -d: -f2)
      note "redis 内存: ${mem:-?}"
    else
      fail "AUTH 失败 (密码不对?)"
    fi
  else
    note "(没 sudo 读 $ENV_FILE, 跳过 AUTH 测试)"
  fi
else
  fail "TCP $META_HOST:$META_PORT 不通"
fi

# ============================================================
echo
info "=== JFS internal stats ==="
# ============================================================
STATS_FILE="$JFS_MOUNT/.stats"
if [[ -r "$STATS_FILE" ]]; then
  S=$(cat "$STATS_FILE" 2>/dev/null)
  _v() { echo "$S" | awk -v k="juicefs_$1" '$1==k{print $2; exit}'; }

  sb=$(_v staging_blocks)
  sw=$(_v staging_writing_blocks)
  sbb=$(_v staging_block_bytes)
  se=$(_v staging_block_errors)
  ou=$(_v object_request_uploading)
  oe=$(_v object_request_errors)

  if [[ "${sb:-0}" == "0" && "${sw:-0}" == "0" ]]; then
    pass "writeback 空闲 (staging=0 writing=0)"
  else
    note "writeback 进行中: staging=${sb:-0} writing=${sw:-0} bytes=$(numfmt --to=iec ${sbb:-0})"
  fi

  if [[ "${se:-0}" == "0" ]]; then
    pass "staging_block_errors=0"
  else
    fail "staging_block_errors=$se"
  fi
  if [[ "${oe:-0}" == "0" ]]; then
    pass "object_request_errors=0"
  else
    fail "object_request_errors=$oe"
  fi
  note "object_request_uploading=${ou:-0} (并发 S3 PUT)"
else
  fail "读不到 $STATS_FILE (没挂载?)"
fi

# ============================================================
echo
info "=== Sidecar ($JFS_LOCAL_DIR) ==="
# ============================================================
if [[ -d "$JFS_LOCAL_DIR" ]]; then
  for d in alpha_dump staging recycle; do
    sym="$JFS_MOUNT/$d"
    local_d="$JFS_LOCAL_DIR/$d"
    if [[ ! -L "$sym" ]]; then
      fail "$d: 不是 symlink (主节点 02-layout 没跑?)"
      continue
    fi
    tgt=$(readlink "$sym")
    if [[ "$tgt" != "$local_d" ]]; then
      fail "$d: symlink -> $tgt, 本机 JFS_LOCAL_DIR 期望 $local_d (改 /etc/juicefs-poc.env)"
    elif [[ ! -d "$local_d" ]]; then
      fail "$d: dangling, 本机缺 $local_d"
    else
      pass "$d: symlink + 本地目录 OK"
    fi
  done
else
  fail "本地 sidecar 缺: $JFS_LOCAL_DIR (跑 join.sh)"
fi

# ============================================================
echo
info "=== Groups + umask ==="
# ============================================================
for g in alpha-core alpha-data; do
  if entry=$(getent group "$g"); then
    gid=$(echo "$entry" | cut -d: -f3)
    members=$(echo "$entry" | cut -d: -f4)
    pass "$g (gid=$gid) [${members:-空}]"
  else
    fail "组 $g 不存在 (跑 02-layout.sh / join.sh)"
  fi
done

UMASK_FILE=/etc/profile.d/ops-umask.sh
if [[ -f "$UMASK_FILE" ]] && grep -q 'umask 0002' "$UMASK_FILE"; then
  pass "$UMASK_FILE: umask 0002"
else
  fail "$UMASK_FILE 缺或不含 umask 0002 (新文件 group 写位失效)"
fi

# ============================================================
echo
info "=== 数据目录概览 ==="
# ============================================================
for d in alpha_src alpha_pnl alpha_feature; do
  T="$JFS_MOUNT/$d"
  if [[ -d "$T" ]]; then
    n=$(find "$T" -mindepth 1 -type f 2>/dev/null | wc -l)
    if (( HAS_SUDO )); then
      b=$(sudo du -sb "$T" 2>/dev/null | awk '{print $1}')
    else
      b=$(du -sb "$T" 2>/dev/null | awk '{print $1}')
    fi
    note "$d: $n files, $(numfmt --to=iec ${b:-0})"
  else
    fail "$T 缺"
  fi
done

# ============================================================
echo
info "=== 汇总 ==="
# ============================================================
if (( FAILS )); then
  _red "  $FAILS / $CHECKS 项异常"
  exit 1
else
  _grn "  $CHECKS 项检查全过"
fi
