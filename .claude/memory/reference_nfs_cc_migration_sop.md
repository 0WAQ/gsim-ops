---
name: reference-nfs-cc-migration-sop
description: cc 数据 NFS 跨 ZFS pool 切换的 SOP 指针 — 替换 cc 副本 + remount 客户端 + idmapping. 详细文档在 docs/ops/nfs-cc-migration.md
metadata: 
  node_type: memory
  type: reference
  originSessionId: 7ff88a50-5f38-42d3-a39c-f97ddef36c12
---

# NFS cc 切换 SOP

完整 SOP: `docs/ops/nfs-cc-migration.md`

## 一句话摘要

跨 ZFS pool 替换 cc 副本 (例: 老 cc_2024 → 新 cc_2024 在 `/tank/vault`), 用 **bind mount** 不用 symlink, 客户端必须 **umount + remount** 拿新 file handle, 否则报 stale。

## 关键坑 (踩过)

1. **NFS export 不跟踪 symlink 替换** — 用 bind mount 不要用 symlink
2. **server 改 inode 后客户端持旧 handle** → "Stale file handle", 必须 client `umount -l + mount` 重挂
3. **wbai uid 不同步** (160=6003, 150=1004), `dr-xr-x---` 这种"对 other hide" 的目录会报 permission denied — 修法是**开 NFSv4 idmapping**, 不要 chmod world
4. **shell heredoc 续行被 `> ` 污染** 把 `/etc/fstab` 搞乱 — 用 `echo ... | sudo tee -a`, 不用 `sudo bash -c 'cat << EOF'`
5. **150 fstab 之前是空的**, 重启会丢挂载, 必须显式加 `_netdev` 行

## 适用场景

- 替换 cc 副本 (新 build 在另一 pool)
- 加新 cc export 给某客户端
- 修复 stale file handle

## How to apply

未来出现"客户端访问 cc_XXXX 异常 / Permission denied / 0 dirs / Stale handle" → 按 SOP 检查清单走一遍。

新挂 cc 副本 / 加客户端 → 按 SOP Step 1-4 走。

不用每次重新踩坑。
