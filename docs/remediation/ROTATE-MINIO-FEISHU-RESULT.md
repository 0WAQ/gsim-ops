# MinIO / Feishu 密钥轮换 — 阶段 0 + 阶段 1 结果

**执行**: server-160 (10.9.100.160), 2026-07-11, 分支 `claude/ops-rotate-and-reconcile`
**rev**: `6a3be56`
**范围**: 本次只做「阶段 0 泄漏面确认(只读)」+「阶段 1 Feishu(低风险)」。
A/B 叉判定、MinIO 切钥窗口、旧钥吊销均**未做**,等判读。

> 红线遵守:本报告所有密钥一律打码(`****` / `(masked)`);与预期不符处立即停,不自行修复。

## 结论速览

- **A/B 叉:定为 A 叉(高危)** —— 2026-07-11 补齐 0b:用户在 160 以 root 跑
  `META_PASSWORD=... juicefs config redis://mymaster,...:26380/0`,**JFS 卷现役
  access-key 前 4 位 == `exte`,即泄漏钥 `exte****-client` 就是 JuiceFS 卷实际在用
  的对象存储钥**。首轮旁证倾向 B 叉被此决定性证据推翻。
- **⚠ 高危面**:泄漏钥 == JFS 卷钥,且 0e 已确认 endpoint 公网可达(200)——
  因子库数据实体(145 alphalib-juicefs 对象)可经公网 + git 历史里的泄漏钥读写。
  A 叉阶段 2-4(建最小权限专用新钥 → `juicefs config` 切钥 → 170→150→144→160
  逐机 remount 验证 → 吊销旧钥)**须走静默窗口**,本会话未做,等排期。
- **0e 公网暴露:`103.237.248.189:39000` 健康探针返回 `200`,公网可达** —— 阶段 5 处置。
- **阶段 1 Feishu:重置须飞书后台(外部登录,本会话无法代做)。** 已定位现役消费方 2 处 + 额外发现 secret 明文散落(详见阶段 1)。

---

## 阶段 0 · 泄漏面确认(只读)

### 0a 泄漏的 MinIO 钥(前 4 位 + 语义)

`git show 15b86de^:config.prod-legacy.yaml`(117-121 行,`s3:` 块),打码输出:

```
  s3:
    endpoint_url: http://103.237.248.189:39000
    access_key_id: exte****-client          # 完整明文含 external-sync 语义,前缀 exte
    secret_access_key: ********(masked)
    bucket: external-sync
```

- **`AK_leak = exte****`**;完整值形如 `external-sync client`(命名带明确用途语义)。
- endpoint 是**公网 IP** `103.237.248.189:39000`,bucket `external-sync`。

### 0b JFS 卷现役钥 —— ✅ 已取到(2026-07-11 补齐,决定叉别)

首轮:手册给的 `juicefs config redis-sentinel://...` 在本机 juicefs `1.3.1` 报
`Invalid meta driver: redis-sentinel`(该版本不认此 scheme;现役 systemd unit
用的是 `redis://mymaster,10.9.100.160,10.9.100.150,10.6.100.144:26380/0`);且改用
实际 meta URL 需 meta redis 密码,密码在 `/etc/juicefs/alphalib-jfs.env`(root-only),
本会话 sudo 无 TTY,首轮未取到。

补齐:用户在 160 以 root 跑(手册修正版 0b 流程):

```
sudo grep META_PASSWORD /etc/juicefs/alphalib-jfs.env          # 取值,不进报告
META_PASSWORD='****' juicefs config \
  "redis://mymaster,10.9.100.160,10.9.100.150,10.6.100.144:26380/0" | grep -i AccessKey
→ AccessKey: exte****(masked)
```

- **JFS 卷现役 access-key 前 4 位 == `exte`,即 == 泄漏钥 `exte****-client`。**
- 这是 A/B 叉的**决定性证据之一**,判定 **A 叉**(泄漏钥即 JFS 卷所用钥)。

### 0c 各机 rclone.conf / env 里的 MinIO 钥

本机(160)`~/.config/rclone/rclone.conf` 存在两个指向公网 endpoint 的 profile:

```
[39000]
  endpoint = http://103.237.248.189:39000
  access_key_id = exte****          # == 泄漏钥(external-sync client)
  secret_access_key = ********(masked)
[jdw]
  endpoint = http://103.237.248.189:39000
  access_key_id = dela****          # 另一独立用户 delayed-signals-jdw
  secret_access_key = ********(masked)
```

- 非 sudo 的 `grep MINIO_ROOT_USER/MINIO_ACCESS /etc/default /etc/systemd/system`
  **无命中**(MinIO 服务端不在 160,在 145,符合拓扑)。sudo 面未查(无权限)。
- **旁证**:泄漏钥 `exte****` 与另一个 `dela****`(delayed-signals-jdw)并列为
  rclone profile,两者都带明确的**独立业务用途命名**,不像 root/JFS 共享钥。

### 0d MinIO 服务端(145)用户/bucket 清单 —— ⚠ 未取到(mc 未安装)

`mc` / `minio` 客户端在 160 **未安装**(`command -v mc` 空;`/usr/local/bin`、
`~` 均无)。无法执行 `mc admin user list` / `mc ls`。

- **MinIO 用户清单(判断 `AK_leak` 是否 == root 用户名)未能获取。**
- 这是 A/B 叉的**决定性证据之二,缺失**。

### 0e 公网暴露检查

```
curl -sm 5 -o /dev/null -w "%{http_code}" http://103.237.248.189:39000/minio/health/live
→ 200   (exit=0)
```

**MinIO 39000 端口公网可达(健康探针 200)。** 记入阶段 5 处置。

### A/B 叉判据汇总(判定:A 叉)

| 判据 | 手册要求 | 结果 |
|---|---|---|
| `AK_leak` 前缀/语义 | 0a | `exte****`,语义 = external-sync **client** |
| `AK_leak` == JFS 卷 access-key? | 0b | **是**(卷钥前 4 位 == `exte`,2026-07-11 补齐) |
| `AK_leak` == MinIO root 用户名? | 0d | 未取到(mc 未装);**已被 0b 定叉,此项不再是必需证据** |
| 公网可达? | 0e | **是(200)** |

**判定:A 叉。** 0b 决定性证据显示泄漏钥就是 JFS 卷现役对象存储钥——首轮基于命名
形态的 B 叉旁证(`external-sync client`、与 `delayed-signals-jdw` 并列独立 profile)
被推翻。走 A 叉全流程(阶段 2-4,含 JFS 逐机切钥),不是 B 叉的"删个废账号"。

**⚠ 这是 A 叉最坏的一支**:泄漏钥 == 因子库数据实体所用钥 + endpoint 公网可达(200)。
git 历史里的废文件删了但钥没吊销,任何人可经公网用它读写 145 上的 alphalib-juicefs
对象(= 全库因子数据)。**旧钥须保活到新钥四机验证通过才吊销**(红线 1),故:

**A 叉待做(静默窗口,本会话未做)**:
1. **阶段 2** 建最小权限专用新钥(`mc admin user add` + 只授 alphalib-juicefs 的 policy),
   新钥独立验证(list + probe 往返),此时生产零影响 —— **前提:145 上有 mc**(160 无,
   见 0d);
2. **阶段 3** `juicefs config --access-key NEW --secret-key NEW` 写卷配置,逐机
   **170→150→144→160** remount + `.rotate_probe` 验证(避开 160 yifei L2 20:00 后);
3. **阶段 4** 吊销旧钥 `exte****-client`(A 叉:若是独立用户直接删,若 == root 则轮换
   root 密码),复验旧钥拒绝 + 四机 status + `ops list` Total;
4. **阶段 5** 更新/删除各机 rclone `[39000]` profile、处置废 bucket `external-sync`、
   **协调关闭 39000 公网入站**。

---

## 阶段 1 · Feishu 轮换

### 1a 泄漏 app 身份

`git show 6a01f99^:ops/infra/notify/feishu_send.py`(~122-123 行),打码:

```
    APP_ID = "cli_****(masked)"        # 泄漏历史中的应用 ID
    APP_SECRET = "********(masked)"
```

### 1b 现役消费方定位(本会话已查,替换须飞书后台)

手册指向 `jiance/feishu_send.py`,但**该路径在 ops 仓库外且当前不存在**
(`/home/wbai/gsim-ops/jiance/` 无)。全盘搜索(排除 vscode 编辑历史、.git)定位到
**真实现役消费方在 `/home/wbai/work/get_trade_list/`**:

| 文件 | 权限 | 内容(打码) | 说明 |
|---|---|---|---|
| `work/get_trade_list/feishu/feishu_send.py` | `660 wbai:wbai` | `APP_ID="cli_****"` `APP_SECRET="****"` | FeishuBot 类 + main;硬编码 app_secret 明文 |
| `work/get_trade_list/monitor.py` | — | `APP_ID="cli_****"` `APP_SECRET="****"` **明文常量** | 实际调度入口,`from feishu.feishu_send import FeishuBot`;**又一份 app_secret 明文** |
| `work/get_trade_list/feishu/feishu_webhook.py` | — | `WEBHOOK_URL=".../hook/****"` | 走 **webhook URL**(非 app_secret),独立机制,不受本次 app_secret 重置影响 |

- **额外发现(记入)**:现役 app 的 `app_id` == 泄漏历史里的同一个应用
  `cli_a9bd****(masked)`(打码);**同一个 app_secret 明文散落在至少 2 个现役
  文件**(`feishu_send.py` + `monitor.py`),权限 `660`。重置后两处都要同步替换。
- 无用户 crontab 命中 feishu/jiance(`crontab -l` 空匹配);调度方式(手动 / 别处
  cron / systemd timer)未进一步定,替换时一并确认。

### 1c 重置动作 —— ⚠ 未执行(需飞书后台外部登录)

重置 `app_secret` 只能在**飞书开放平台后台**操作,本会话无该登录凭证,**无法代做**。
故阶段 1 的实际重置 + 测试消息**未执行**。待你在后台重置后:

1. 后台 → 应用 `cli_****`(即 `cli_a9bd****(masked)`)→ **重置 app_secret**;
2. 同步替换现役两处明文:
   - `/home/wbai/work/get_trade_list/feishu/feishu_send.py`
   - `/home/wbai/work/get_trade_list/monitor.py`
   建议顺手把明文 secret 挪出源码(读环境变量 / 0600 配置文件),文件权限收 `0600`;
3. 发一条测试消息验证;
4. 若该 app 已无人用:后台直接**禁用应用**,则跳过 2-3,记录即可。

（`feishu_webhook.py` 走 webhook URL,不吃 app_secret,本次不受影响;如需一并轮换
webhook 另议。）

---

## 未做 / 等确认(红线)

- **阶段 0 的 A/B 叉判定**:✅ 已定 **A 叉**(2026-07-11 补齐 0b,卷钥前缀 == `exte`
  == 泄漏钥;见「A/B 叉判据汇总」)。
- **阶段 1 Feishu 重置 + 测试消息**:需飞书后台外部登录,未执行。
- **阶段 2-5**(新钥 / JFS 切钥 / 旧钥吊销 / 收尾)**全部未做**;A 叉切钥须静默窗口
  (避开 160 yifei L2 20:00 后、无 ops check 在跑)+ 145 上备好 mc,再排期。
- **0e 公网暴露(200)** 待阶段 5 与网络管理协调关闭 39000 公网入站。


