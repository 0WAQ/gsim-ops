# MIGRATE 170 挂载点迁移 —— 执行结果(阶段性:prep 完成,卡在两处需人工)

日期 2026-07-11。分支 `claude/ops-setup` @ `a0d639a`。
执行:160 经 SSH 远程(**无 TTY、sudo 不可用**)完成全部**免 sudo 的 prep**;
迁移正片(`--migrate-mount`)与补建(`setup`)因需 root+TTY **未执行,留 170 本机 TTY**。
另发现一处 runbook 未预期的阻断(170 缺 PG 密码),一并记录。

## TL;DR

- ✅ 步骤 1(拉分支 / 重装 ops):完成。170 现 HEAD `a0d639a`,`~/.local/bin/ops`
  已含 `--migrate-mount` 代码。
- ✅ 步骤 2(`setup --check` 捕获 mount FAIL):完成,**原文见下**。mount 项 FAIL 且
  detail 正如预期写"挂在 /ext4/alphalib,声明是 /nvme125/alphalib;跑 --migrate-mount"。
- ⛔ 步骤 3(`--migrate-mount`):**未执行**。需 sudo 密码 + 真 TTY,SSH 免密跑不了。
- ⛔ 步骤 4/5(`setup` 补建 / 复检 / `ops list`):**未执行**,且即使迁移成功也会被
  **PG 密码缺失**卡住(见"阻断 B")。

## 阻断 A:sudo 需 TTY + 密码(已知)

`--migrate-mount` 写 `/etc/systemd/system/juicefs-alphalib.service` + `/etc/juicefs-poc.env`
并 `systemctl stop/start`;plain `ops setup` 补建走 sudo self-elevate。二者都需 root。
DISCOVER 已实测 170 **无 NOPASSWD**,SSH 无 TTY。→ **必须 170 本机键盘执行**。

迁移编排前置守卫在此前 DISCOVER 已满足:writeback 已排干(`staging=0`)、目标父目录
`/nvme125` 在位、env+unit 在位。所以本机 TTY 上直接跑 `--migrate-mount` 应能过守卫。

## 阻断 B:170 缺 PG 密码(runbook 未预期)⚠

`setup --check` 里两项 PG 相关检查 FAIL:

```
✘ Postgres 连接 + 三表   PG 不可达或三表缺失: connection failed: connection to
                        server at "10.9.100.160", port 15432 failed:
                        fe_sendauth: no password supplied
✘ 跨机因子锁往返         PG 不可达,锁不可用: (同上 no password supplied)
```

根因:config.yaml `state.postgres.password_file = /home/wbai/gsim-ops/scripts/postgres/.env`
(key `OPS_PG_PASSWORD`),该文件 **gitignored**(`git check-ignore` 命中),
git push **不带**它。160 上存在(`-rw------- 65 bytes`),170 上**不存在**。
DISCOVER 阶段测的是 PG **端口**可达(裸 TCP OK),但 ops 缺密码连不上。
拓扑记录里 PG 密码分发给过 150/144(升级窗口 scp),**170 从未纳入**。

**后果**:即便挂载迁移成功,步骤 4 的 `setup --check` 也到不了 FAIL 0(PG 两项恒红),
步骤 5 `ops list`(读 PG,预期 Total 8252)会直接失败。→ **迁移前需先把
`scripts/postgres/.env` 分发到 170**(免 sudo,160 scp 即可,待用户确认再做)。

---

## 逐条原文

### 步骤 1a:分支同步(160 → 170 SSH 直推,170 无 GitHub 出口)

```
# 在 160: git push ssh://10.9.100.170/home/wbai/gsim-ops claude/ops-setup:claude/ops-setup
 * [new branch]      claude/ops-setup -> claude/ops-setup
# 在 170: git checkout claude/ops-setup
Switched to branch 'claude/ops-setup'
=== HEAD ===
a0d639a feat(setup): --migrate-mount —— JFS 挂载点迁移(声明变更收敛)+ 170 声明 /nvme125
```

### 步骤 1b:重装 ops(uv 不在 PATH,用绝对路径)

```
$ ~/.local/bin/uv tool install --reinstall ~/gsim-ops
 ~ ops==0.1.1 (from file:///home/wbai/gsim-ops)
 ~ psycopg==3.3.4
 ...
Installed 1 executable: ops
warning: `/home/wbai/.local/bin` is not on your PATH. ...
```

### 步骤 2:`ops setup --check`(捕获 mount FAIL —— 从 repo 目录跑,否则找不到 config.yaml)

> 注:首次在 `$HOME` 跑 `~/.local/bin/ops setup --check` 报
> `FileNotFoundError: '/home/wbai/config.yaml'`(默认 config 路径是相对的);
> `cd ~/gsim-ops` 后正常。

```
host: server-170  路径来源: hosts 声明  模式: check(只读)

     check                               detail
 ──────────────────────────────────────────────────────────────────────────────
 ✔   hosts 声明命中                      hosts.server-170 命中,路径按声明解析
 ✘   alphalib JFS 挂载                   JFS 卷 'alphalib' 挂在 /ext4/alphalib,
                                         声明是 /nvme125/alphalib;跑 `ops setup
                                         --migrate-mount` 迁移
 ✘   共享目录 (src/pnl/feature)          alpha_src: 缺失; alpha_pnl: 缺失; alpha_feature: 缺失
 ✘   bcorr 分流池 (automated/manual)     pnl_automated: 缺失; pnl_manual: 缺失
 ✘   staging 形态                        staging 软链缺失(应指 /nvme125/alphalib.local/staging)
 ✘   alpha_dump 软链 → sidecar           alpha_dump 软链缺失(应指 /nvme125/alphalib.local/alpha_dump)
 ⚠   /mnt/storage/alphalib 兼容软链      /mnt/storage/alphalib 软链缺失(老脚本/固定路径文档经它访问本机 alphalib)
 ✔   权限组 alpha-core/alpha-data        alpha-core/alpha-data 在位
 ✔   顶层目录权限模型                    顶层 owner/组/setgid 符合模型
 ✘   Postgres 连接 + 三表                PG 不可达或三表缺失: ... fe_sendauth: no password supplied
 ✘   跨机因子锁往返                      PG 不可达,锁不可用: ... fe_sendauth: no password supplied
 ✔   nio_data_path 数据                  在位
 ⚠   dropbox 投递目录(submit 节点需要)   dropbox_path: /mnt/storage/dropbox
 ✔   gsim 工具链(check 节点需要)         在位

FAIL: 7  WARN: 2  已补建: 0  (共 14 项)
=== exit: 1 ===
```

> mount 项 FAIL + detail 完全符合 runbook 预期。src/pnl/feature/staging/dump/bcorr
> 的 FAIL 均因 `/nvme125/alphalib` 尚未挂载(迁移后经 `setup` 补建即绿);
> `/mnt/storage/alphalib` WARN 迁移收尾会重指;PG 两项见阻断 B。

### 步骤 3-5:未执行

- 步骤 3 `--migrate-mount`:需本机 TTY(阻断 A),未跑。
- 步骤 4/5 `setup` 补建 / 复检 / `ops list`:未跑;且被阻断 B 挡住,须先分发 PG 密码。

---

## 建议下一步(待用户裁决)

1. **先分发 PG 密码到 170**(免 sudo):`scp scripts/postgres/.env` 160 → 170
   同路径,`chmod 600`。这是 runbook 隐含前置,不做则步骤 4/5 恒败。
2. **迁移正片在 170 本机 TTY 执行**(需 sudo 密码):
   ```
   cd ~/gsim-ops
   ~/.local/bin/ops setup --migrate-mount   # 确认计划按 y,sudo 输密码
   ~/.local/bin/ops setup                    # 补建组/软链,sudo self-elevate
   ~/.local/bin/ops setup --check            # 预期 FAIL 0
   ~/.local/bin/ops list 2>/dev/null | tail -1   # 预期 Total 8252
   ```
   逐行输出回填本文件"步骤 3-5"节。
3. **红线不变**:`/nvme125` 上已有 alpha_dump/alpha_pnl/checkpoint/datasvc 四目录不碰;
   `/ext4` 旧址(空挂载点 / 旧 cache / 搬空 sidecar)judged 后再清。任一步中止/回滚立即停。
