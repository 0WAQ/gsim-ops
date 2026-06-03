#!/usr/bin/env bash
# 主节点全套部署: 00 -> 01 -> 02 -> 03 -> 04, 任一失败停。
# 不动数据迁移 (05) 和服务启动 — 显示在 DONE 提示里让用户决定。
#
# 用法:
#   sudo -E bash bootstrap-primary.sh
#
# Client 节点用 join.sh,不要跑这个。

set -euo pipefail
cd "$(dirname "$0")"
source ./_lib.sh

require_sudo

STAGES=(
  00-install.sh
  01-provision.sh
  02-layout.sh
  03-redis.sh
  04-systemd.sh
)

for s in "${STAGES[@]}"; do
  [[ -x "./$s" ]] || err "缺 ./$s"
done

for s in "${STAGES[@]}"; do
  echo
  _grn "######################## $s ########################"
  echo
  sudo -E bash "./$s"
done

source ./config.sh
SVC="juicefs-${JFS_NAME}.service"

echo
_grn "DONE. 主节点部署完成。"
echo
_grn "下一步:"
_grn "  1. 启动服务         sudo systemctl start $SVC"
_grn "  2. 健康检查         sudo bash status.sh"
_grn "  3. 数据迁移 (可选)  sudo -E bash 05-migrate.sh --dry-run    # 先看量"
_grn "                      sudo -E bash 05-migrate.sh              # 实际跑"
_grn "  4. Client 接入      取密码 + scp 脚本目录, 在 client 跑"
_grn "                      sudo bash /tmp/juicefs-poc/join.sh \\"
_grn "                        --meta-host <主节点 IP> \\"
_grn "                        --mount <client 挂载点> \\"
_grn "                        --cache <client cache 路径>"
