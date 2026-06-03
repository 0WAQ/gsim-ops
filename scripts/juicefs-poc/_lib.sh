#!/usr/bin/env bash
# 公共自检 helpers。source 进各脚本顶部。
# 不要直接跑。

# 颜色 / 提示
_red()   { printf '\033[31m%s\033[0m\n' "$*" >&2; }
_yel()   { printf '\033[33m%s\033[0m\n' "$*" >&2; }
_grn()   { printf '\033[32m%s\033[0m\n' "$*"; }
err()    { _red  "ERROR: $*"; exit 1; }
warn()   { _yel  "WARN:  $*"; }
info()   { _grn  "$*"; }

# 要求当前用户能 sudo(密码缓存/NOPASSWD 都行,只要 sudo -n true 之后能行)
require_sudo() {
  if [[ $EUID -eq 0 ]]; then return; fi
  if ! command -v sudo >/dev/null; then err "需要 sudo 但系统没装"; fi
  # 先试 cache;不行就提示一次让用户输
  if ! sudo -n true 2>/dev/null; then
    info "需要 sudo 权限,会提示输入密码:"
    sudo -v || err "sudo 鉴权失败"
  fi
}

# 要求二进制存在,否则告诉用户怎么装
require_bin() {
  local bin=$1 hint=${2:-}
  command -v "$bin" >/dev/null || err "缺二进制 '$bin'${hint:+,$hint}"
}

# 要求 systemd 在跑
require_systemd() {
  [[ -d /run/systemd/system ]] || err "本机没用 systemd"
}

# 要求路径存在(或可创建)
require_dir() {
  local d=$1
  [[ -d "$d" ]] || err "目录不存在: $d"
}

# 要求挂载点
require_mountpoint() {
  local mp=$1
  mountpoint -q "$mp" || err "不是挂载点: $mp"
}

# 要求能 ping 通某 host:port(用 bash /dev/tcp)
require_tcp() {
  local host=$1 port=$2 timeout=${3:-3}
  if ! timeout "$timeout" bash -c "echo > /dev/tcp/$host/$port" 2>/dev/null; then
    err "TCP 不通: $host:$port"
  fi
}
