# MinIO / Feishu 密钥轮换执行手册

**背景**:两组凭证曾提交入库,文件已删但 **git 历史仍在,删除 ≠ 吊销**:

| 泄漏物 | 历史位置(执行者本地 `git show` 查看,**严禁把明文抄进报告**) | 影响面 |
|---|---|---|
| MinIO access_key_id + secret_access_key | `git show 15b86de^:config.prod-legacy.yaml`(117-121 行,`s3:` 块) | endpoint 是**公网 IP** `103.237.248.189:39000`,bucket `external-sync`;若该钥同时是 MinIO root 或 JFS 卷所用钥,则 145 上整个对象存储(含 alphalib-juicefs,即因子库数据实体)暴露 |
| Feishu APP_ID + APP_SECRET | `git show 6a01f99^:ops/infra/notify/feishu_send.py`(~122-123 行) | 飞书应用消息权限 |

**红线**:
1. **旧钥保活直到新钥在全部挂载点验证通过** —— 提前吊销 = 因子库全线掉挂载;
2. 不动 redis / sentinel(JFS metadata,与本次对象存储凭证无关);
3. **报告里严禁出现任何密钥明文**(新旧都算)——贴命令输出前把密钥打码 `****`;
4. 每步贴命令原文输出(打码后);实际与预期不符**立即停**,不自行修复;
5. JFS 切钥涉及逐机 remount,须在**静默窗口**执行(无 ops check 在跑、160 的
   yifei L2 生产 20:00 后避开)。

---

## 阶段 0 · 泄漏面确认(只读,随时可做)

目的:确定泄漏的 MinIO 钥和现役钥的关系,决定走 A 叉(重钥,含 JFS 切钥)
还是 B 叉(废弃独立旧钥,不动 JFS)。

```bash
cd ~/gsim-ops
# 0a. 泄漏钥的 access_key_id(本地看,记住前 4 位即可,报告写 "AK_leak=xxxx****")
git show 15b86de^:config.prod-legacy.yaml | sed -n 117,121p

# 0b. JFS 卷现役钥(160;secret 会打码显示)
juicefs config redis-sentinel://10.9.100.160:26380,10.9.100.150:26380,10.6.100.144:26380/mymaster/0 | head -30

# 0c. 各机 rclone.conf / env 里现存的 MinIO 钥(160/150/144/147 各查一遍)
grep -n "access_key_id" ~/.config/rclone/rclone.conf 2>/dev/null | sed -E 's/=.{4}/= xxxx/'
sudo grep -rn "MINIO_ROOT_USER\|MINIO_ACCESS" /etc/default /etc/systemd/system 2>/dev/null | head

# 0d. MinIO 服务端(145)的用户清单(mc alias 用现役 root 钥,按本机习惯配置)
mc alias set m145 http://10.9.100.145:39000 <当前root用户> <当前root密码>   # 命令本身不进报告
mc admin user list m145
mc ls m145                        # bucket 清单:预期 alphalib-juicefs + external-sync(+其它?记录)

# 0e. 公网暴露检查(从任一有公网出口的机器)
curl -sm 5 -o /dev/null -w "%{http_code}\n" http://103.237.248.189:39000/minio/health/live ; echo "exit=$?"
```

**判定**(写进报告):
- `AK_leak` == MinIO root 用户名,或 == 0b 里 JFS 卷的 access-key → **走 A 叉(阶段 2-4 全做)**;
- `AK_leak` 是独立用户(仅 external-sync 用途)且 ≠ JFS 钥 → **走 B 叉(跳过阶段 3,阶段 4 只吊销该用户)**;
- 0e 若公网可达(200):记录,阶段 5 处置。

## 阶段 1 · Feishu 轮换(独立,低风险,先做)

1. 飞书开放平台后台 → 对应 APP_ID 的应用(ID 从 0a 同款方式本地查看)→
   **重置 app_secret**;
2. 更新现役消费方:`grep -rn "APP_SECRET\|app_secret" /home/wbai/gsim-ops/jiance/`
   (config 历史指向 `jiance/feishu_send.py`,在 ops 仓库外;若还有别的 cron 用它一并查);
   替换为新 secret(文件权限收 0600);
3. 发一条测试消息验证;
4. 若该应用已无人用:直接在后台**禁用应用**,记录即可,不用改脚本。

预期报告物:重置完成 + 测试消息送达(或"应用已禁用"),无明文。

## 阶段 2 · 新钥创建(A 叉;B 叉跳过)

**原则:JFS 不再用 root/共享钥,建专用最小权限用户。**

```bash
# 2a. 生成新凭证(本地生成,不进报告): openssl rand -hex 16 两次,记为 NEW_AK / NEW_SK
# 2b. 建专用用户 + 只授 alphalib-juicefs 读写
mc admin user add m145 NEW_AK NEW_SK
cat > /tmp/jfs-alphalib-rw.json <<'EOF'
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":["s3:*"],
  "Resource":["arn:aws:s3:::alphalib-juicefs","arn:aws:s3:::alphalib-juicefs/*"]}]}
EOF
mc admin policy create m145 jfs-alphalib-rw /tmp/jfs-alphalib-rw.json
mc admin policy attach m145 jfs-alphalib-rw --user NEW_AK

# 2c. 新钥独立验证(不碰 JFS):
mc alias set m145new http://10.9.100.145:39000 NEW_AK NEW_SK
mc ls m145new/alphalib-juicefs | head -3          # 能列
echo probe | mc pipe m145new/alphalib-juicefs/__rotate_probe && mc rm m145new/alphalib-juicefs/__rotate_probe
```

任一步失败:停(此时生产零影响)。

## 阶段 3 · JFS 切钥 + 逐机滚动 remount(A 叉;静默窗口)

```bash
# 3a. 写入卷配置(metadata;此刻旧钥仍有效,运行中的挂载不受影响)
juicefs config redis-sentinel://10.9.100.160:26380,10.9.100.150:26380,10.6.100.144:26380/mymaster/0 \
  --access-key NEW_AK --secret-key NEW_SK
```

3b. **逐机** remount + 验证,顺序 **170 → 150 → 144 → 160**(消费最少的先,
master 最后)。每机:

```bash
# 等 writeback 排干(scripts/juicefs-poc/status.sh 看;或 stat 挂载点 .stats)
bash ~/gsim-ops/scripts/juicefs-poc/status.sh
sudo systemctl restart juicefs-alphalib.service
mountpoint <本机挂载点>/                                  # 160/150 /tank/vault/alphalib;144 /storage/vault/alphalib;170 /ext4/alphalib
echo rotate-$(hostname)-$(date +%s) | sudo tee <挂载点>/.rotate_probe && cat <挂载点>/.rotate_probe && sudo rm <挂载点>/.rotate_probe
```

任一机 remount 后读写失败 → **回滚**:`juicefs config ... --access-key OLD --secret-key OLD`
+ 重启该机 unit,停止报告。

3c. 四机全过后,160 上跑一遍业务级验证:
```bash
uv run ops list 2>/dev/null | tail -1     # Total 与基线一致
```

## 阶段 4 · 旧钥吊销 + 复验

```bash
# A 叉:若旧钥是独立用户 → 直接删;若旧钥是 root → 轮换 root 密码
#   (145 上 MinIO 服务的 MINIO_ROOT_PASSWORD env / 配置文件改掉后 restart minio;
#    注意 restart minio 期间 JFS 对象请求会短暂失败,选静默窗口,JFS 客户端自带重试)
mc admin user remove m145 AK_leak                 # 独立用户情形
# 或:编辑 145 的 minio 服务 env → systemctl restart minio

# B 叉:仅吊销泄漏的独立用户(JFS 全程未动):
mc admin user remove m145 AK_leak

# 复验:旧钥确实死了(预期 AccessDenied / InvalidAccessKeyId)
mc alias set m145old http://10.9.100.145:39000 AK_leak OLD_SK && mc ls m145old 2>&1 | head -2
# 四机挂载仍健康(每机):
bash ~/gsim-ops/scripts/juicefs-poc/status.sh | tail -5
# 业务复验(160):
uv run ops list 2>/dev/null | tail -1
```

## 阶段 5 · 收尾

1. 各机 `~/.config/rclone/rclone.conf` 的 `[39000]` profile 更新为新钥
   (或删除该 profile,如已无消费方);文件权限 0600;
2. `external-sync` bucket:sync 栈已退役,确认无人读后建议删除
   (`mc rb --force m145/external-sync`)——**先 `mc ls` 确认内容再删,报告记录**;
3. 公网暴露(0e 若 200):与网络管理协调,关闭 39000 的公网入站
   (sync 退役后无公网消费方);
4. git 历史不重写:旧钥吊销后历史里的是废钥,重写历史对多机克隆破坏性大,不做。

## 阶段 6 · 报告

写入 `docs/remediation/ROTATE-MINIO-FEISHU-RESULT.md`,push 到
`claude/ops-rotate-and-reconcile` 分支。逐步一行 + 关键命令原文(**密钥一律打码**):
阶段 0 的判定(A/B 叉 + 依据)、0e 公网检查结果、阶段 1 测试消息、阶段 2 probe
往返、阶段 3 每机 remount 验证行、阶段 4 旧钥拒绝原文 + 四机 status 尾行 +
Total、阶段 5 处置记录。任何一步不符:停在那一步,报告写到哪算哪。
