# cc 数据 NFS 切换 SOP

跨 ZFS pool 替换 cc 副本 (例: 旧 `/datasvc/data/cc_2024` → 新 `/tank/vault/datasvc/data/cc_2024`), 客户端无中断。

适用场景: 新 build 的 cc 副本在另一个 pool 上, 原路径要保持不变, 已有 NFS 客户端正在挂着用。

## 关键约束

| 约束 | 含义 |
|---|---|
| `/datasvc/data` 根盘满 | 不能物理移动数据进去, 必须留在 `/tank/vault` 用 indirection |
| NFS 客户端不能中断太久 | 不能停 NFS server, 不能 export 长时间空 |
| `/etc/exports` 用路径名 export | 客户端持有的是 file handle (inode), 不是路径名 |
| 不同机 wbai uid 可能不同 | 160=6003, 150=1004 等, sec=sys 默认按数字 uid 匹配 |
| 部分 cc 副本权限 `dr-xr-x---` | 比如 cc_2025 故意 hide other, 必须靠 idmapping 走用户名映射 |

## ❌ 错误做法 (会踩坑)

```bash
# 方案 A: 纯 symlink (NFS 不友好)
rm /datasvc/data/cc_2024
ln -sfn /tank/vault/datasvc/data/cc_2024 /datasvc/data/cc_2024
# 坑: NFS export 当初记的是被替换前的目录 inode, 客户端继续读旧数据无感
```

```bash
# 方案 B: 没 umount 客户端直接动 server
rm /datasvc/data/cc_2024
mkdir /datasvc/data/cc_2024
mount --bind /tank/vault/... /datasvc/data/cc_2024
exportfs -ra
# 坑: 客户端持有旧 file handle, 访问报 "Stale file handle"
# 必须客户端 umount + 重 mount 才能拿新 handle
```

## ✅ 推荐做法 — Server 端 bind mount + 客户端 remount

### Step 1: server 端 (160) 准备新位置

```bash
# 假设新数据已经在 /tank/vault/datasvc/data/cc_2024
# 老的先 rename 备份
sudo mv /datasvc/data/cc_2024 /datasvc/data/cc_2024_unused
# (cc_2024_unused 等观察期过了再 rm -rf 腾盘)

# 建空 mount point + bind mount
sudo mkdir /datasvc/data/cc_2024
sudo mount --bind /tank/vault/datasvc/data/cc_2024 /datasvc/data/cc_2024

# 持久化 (重启不丢)
echo '/tank/vault/datasvc/data/cc_2024  /datasvc/data/cc_2024  none  bind  0  0' \
    | sudo tee -a /etc/fstab
```

### Step 2: server 端加 / 验证 NFS export

```bash
# 如果之前没 export 过这个路径 (cc_2025 第一次加):
sudo bash -c "echo '/datasvc/data/cc_2025 10.9.100.145(rw,sync,no_subtree_check,root_squash) 10.9.100.0/24(ro,sync,no_subtree_check,root_squash)' >> /etc/exports"

# Re-export 触发 server 端 file handle 更新
sudo exportfs -ra

# 验证 export 表
showmount -e localhost
```

### Step 3: 每个 NFS 客户端 (150, 145, ...) 强制 remount

```bash
ssh <client>
# 看现在 mount 是不是还在 (stale 状态下 mount 显示在但 ls 报 stale)
mount | grep cc_2024
ls /cc_2024 2>&1  # 期望: 报 "Stale file handle"

# umount + 重挂
sudo umount -l /cc_2024   # lazy, 即便有 mmap 进程也能卸
sudo mount /cc_2024       # 走 fstab; 或手动 mount -t nfs4 ...

# 验证
ls /cc_2024 | wc -l       # 应该等于 server 端新 cc 的目录数
```

### Step 4: 客户端 fstab 持久化

**`/etc/fstab` 要有, 否则重启丢挂载**:

```bash
# 用 tee, 别用 heredoc (避免 shell `>` 续行污染)
echo '10.9.100.160:/datasvc/data/cc_2024  /cc_2024  nfs4  rw,relatime,vers=4.2,hard,_netdev  0  0' \
    | sudo tee -a /etc/fstab

# reload + 测试 fstab 解析
sudo systemctl daemon-reload
sudo mount -a   # 应该幂等不报错
```

`_netdev` 必须有, 让系统等网络好了再挂。

## 处理新副本权限 `dr-xr-x---` (例: cc_2025 hide other)

### 现象

```bash
# 150 上
ls /cc_2025
# Permission denied
```

### 根因

160 上 wbai uid 跟 150 上 wbai uid 不一样, NFS sec=sys 按数字 uid 走, server 看 150 来的 uid 不是 owner 也不在 group, 走 "other" → `---` 拒绝。

### ❌ 错误修法

```bash
sudo chmod -R o+rX /datasvc/data/cc_2025/
# 坑: 破坏了原本"对 other hide" 的设计
```

### ✅ 正确修法 — 启用 NFSv4 idmapping (按用户名映射)

```bash
# === server 160 ===
echo N | sudo tee /sys/module/nfsd/parameters/nfs4_disable_idmapping

# 持久化
echo 'options nfsd nfs4_disable_idmapping=0' | sudo tee /etc/modprobe.d/nfsd-idmap.conf

# 看 domain (两边必须一致, 默认 localdomain)
grep -E '^Domain' /etc/idmapd.conf

# 重启 idmapd
sudo systemctl restart nfs-idmapd
```

```bash
# === client 150 ===
echo N | sudo tee /sys/module/nfs/parameters/nfs4_disable_idmapping  # 注意 nfs 不是 nfsd
echo 'options nfs nfs4_disable_idmapping=0' | sudo tee /etc/modprobe.d/nfs-idmap.conf

grep -E '^Domain' /etc/idmapd.conf  # 跟 server 一致

sudo systemctl restart nfs-idmapd
sudo umount -l /cc_2025 && sudo mount /cc_2025
```

之后 150 上 wbai (uid 1004) NFS 请求会被 server 识别成 wbai (uid 6003), 原生 `dr-xr-x---` 也能正常访问, 不用 chmod world。

## 检查清单 (新挂 / 改挂 cc 时跑一遍)

```bash
# Server 端 (160)
findmnt /datasvc/data/cc_XXXX            # 期望: bind mount 显示
ls /datasvc/data/cc_XXXX | wc -l         # 期望: 跟源一致
grep cc_XXXX /etc/exports                # 期望: 有
showmount -e localhost | grep cc_XXXX    # 期望: 有
grep cc_XXXX /etc/fstab                  # 期望: 有 bind 行

# 每个 client
mount | grep cc_XXXX                     # 期望: 挂着
ls /cc_XXXX | wc -l                      # 期望: 跟 server 一致, 不是 0 不是 stale
grep cc_XXXX /etc/fstab                  # 期望: 有 NFS 行
```

## 常见错误 + 修复

| 症状 | 根因 | 修法 |
|---|---|---|
| `ls /cc_XXXX` 报 "Stale file handle" | server 端动了 inode, 客户端持旧 handle | client `umount -l + mount` |
| `ls /cc_XXXX` 空 (0 dirs) | 没真正挂上 (mount entry 在但底层没数据) | client `umount + mount` |
| `ls /cc_XXXX` 报 "Permission denied" | uid 不匹配 + 目录是 `dr-xr-x---` | 开 NFSv4 idmapping (见上面) |
| 客户端 ls 跟 server 不一致 | NFS attr cache 顽固 / client 持旧 handle | `umount -l + mount`; 或 `echo 3 > /proc/sys/vm/drop_caches` |
| 客户端重启后 mount 全丢 | fstab 没记 | 加 fstab + `_netdev` |
| `sudo bash -c 'cat >>... << EOF'` 把 fstab 搞乱 | shell 续行 `> ` 污染 heredoc 内容 | 改用 `echo ... | sudo tee -a`, 或 `sudo vi` |

## 物理空间释放 — `cc_XXXX_unused` 何时清

bind mount 后, 真实数据仍在 `/tank/vault`, 旧的 `/datasvc/data/cc_XXXX_unused` 还占根盘。一周观察期 (客户端无异常) 后:

```bash
du -sh /datasvc/data/cc_2024_unused    # 看占多少
sudo rm -rf /datasvc/data/cc_2024_unused
```

500GB cc_2024 删完根盘从 86% 降到合理水平。

---

## 历史

| 日期 | 操作 |
|---|---|
| 2026-06-08 | 用此 SOP 把 `/datasvc/data/cc_2024` 切到 `/tank/vault/datasvc/data/cc_2024` + 新增 cc_2025 挂载到 150, 中间踩了 stale handle / heredoc 污染 / 权限不一致几个坑 (本文档由此而来) |
