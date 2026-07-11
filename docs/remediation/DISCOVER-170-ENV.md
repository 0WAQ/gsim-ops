# 170 环境采集(只读)—— ops setup 挂载点迁移功能的实现输入

**背景**:170 的 JFS 挂载点要从 `/ext4/alphalib` 迁到 `/nvme125/alphalib`
(cache/sidecar 同盘)。迁移动作将由 `ops setup --migrate-mount`(开发中)
执行 —— 本采集的输出决定该功能的实现细节(env 键名、unit 结构、渲染方式),
**不猜,以实测为准**。

**红线**:本文档全部命令**只读**(cat/ls/df/du/grep/systemctl cat/lsof),
不改任何东西;输出里若出现密码/密钥一律打码 `****` 再进报告。

在 **170(10.9.100.170)** 上逐条执行,输出原文(打码后)回填到
`docs/remediation/DISCOVER-170-ENV-RESULT.md`,push 到 `claude/ops-setup` 分支
(170 无 GitHub 出口的话,照 150 的办法经 160 SSH 直推)。

```bash
# 1. 现役 JFS 部署声明(env 键名和现值 —— migrate 要改写它)
cat /etc/juicefs-poc.env
sudo ls -la /etc/juicefs/ && sudo grep -c . /etc/juicefs/*.env   # 文件名+行数即可,内容勿贴

# 2. systemd unit 结构(migrate 判断"改 env 就够"还是"要重渲染 unit")
systemctl cat juicefs-alphalib
systemctl is-enabled juicefs-alphalib; systemctl is-active juicefs-alphalib

# 3. 挂载现状 + 健康基线
grep -i juicefs /proc/mounts
cd ~/gsim-ops 2>/dev/null && bash scripts/juicefs-poc/status.sh; echo "status.sh exit=$?"
# (~/gsim-ops 不存在则记录"170 无 repo",跳过 status.sh)

# 4. 目标盘状态
df -h /nvme125
ls -la /nvme125/ | head -15
mount | grep nvme125       # 它自己是什么文件系统、挂载参数

# 5. 待搬存量(sidecar + cache 的体量)
du -sh /ext4/alphalib.local 2>/dev/null; ls -la /ext4/alphalib.local/ 2>/dev/null
du -sh /ext4/juicefs-cache 2>/dev/null || true
# cache 目录名若与 env 里 JFS_CACHE_DIR 不同,以 env 为准再 du 一次

# 6. 兼容软链与占用
ls -la /mnt/storage/alphalib 2>/dev/null; ls -la /mnt/storage/ 2>/dev/null | head
sudo lsof +D /ext4/alphalib 2>/dev/null | head -10; echo "lsof exit=$?"

# 7. ops 侧现状(migrate 之后 setup 要在这台跑)
ls ~/gsim-ops 2>/dev/null | head -3; command -v uv; command -v ops; ls ~/.local/bin/ops 2>/dev/null
sudo -n true 2>&1; echo "sudo-nopasswd exit=$?"
hostname
# PG 可达性(ops setup 的 pg 检查将用到;5 秒即返)
timeout 5 bash -c 'cat < /dev/null > /dev/tcp/10.9.100.160/15432' && echo PG-PORT-OK || echo PG-PORT-FAIL

# 8. 权限组现状
getent group alpha-core alpha-data
```

**报告纪律**:逐条贴原文(打码);任何命令报错也原样贴(报错本身是采集结果,
不用修);不做任何写操作。
