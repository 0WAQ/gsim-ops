# 共享 staging + 170 消费机部署手册

**目标**:落地 `docs/shared-staging-queue.md` 的框架 —— staging 从本机 sidecar
迁 JFS 共享 + 170 部署 ops 当消费机。完成后:任意机器 submit,170 跑 check,
任意机器看结果。

**红线**:
1. 阶段 1(staging 共享化)须在**短窗口**内做:开始前确认三机(160/150/144)
   **无 in-flight submit/check/restage/cancel/clear**,窗口内禁止这些命令;
2. 金丝雀写操作只限 `AlphaWbaiCanary001`;
3. 每步贴命令原文(密码类打码);不符即停;
4. 不动 redis/sentinel;不碰 alpha_src/alpha_pnl/alpha_feature 的任何存量。

---

## 阶段 0 · 170 部署(无窗口,随时可做)

```bash
# 0a. 前置确认(170 上)
mountpoint /ext4/alphalib                        # 预期 is a mountpoint(06-24 已接入)
ls /ext4/alphalib/ | head                        # 能看到 alpha_src 等
ls /usr/local/gsim/run.py /usr/local/gsim/dataops/bcorr   # gsim 在位
ls /datasvc/data/ | head                         # 数据在位(nio_data_path 用哪套见 0c)
ls ${gsim_home:-/usr/local/gsim}/pnl_prod | head -3   # bcorr legacy 回退池在位

# 0b. 部署 ops
git clone <repo> ~/gsim-ops && cd ~/gsim-ops && uv sync
# PG 凭证:从 160 按 2026-07-08 分发同款方式 scp 密码文件(root-only 0600),
# 路径与 config.yaml state.postgres 的 password_file 一致

# 0c. 每机路径适配(persistent,写 ~/.profile 或 /etc/environment)
export OPS_ALPHALIB_ROOT=/ext4/alphalib
# nio_data_path:config.yaml 默认 /datasvc/data/cc_2025;确认 170 同路径有数据,
# 不同则 export OPS_NIO_DATA_PATH=<170 实际路径> 并在报告记录

# 0d. 只读冒烟
uv run ops list 2>/dev/null | tail -1            # Total 与 160 基线一致
uv run ops status | tail -3
```

sudo:ops 写命令会 self-elevate,确认 wbai 在 170 有 sudo。
任何一步不符:停(此时生产零影响)。

## 阶段 1 · staging 共享化(短窗口)

**事实基础**:挂载点内的 `alphalib/staging` 软链是单一 JFS 对象(相对 target
`../alphalib.local/staging` 逃出挂载点落各机本地)——删软链/建目录**一次全局
生效**,不用逐机操作;但**各机 sidecar 里的存量**要逐机搬。

```bash
# 1a. 窗口确认(160 上;确保没人在跑)
uv run ops status --status submitted | tail -3   # 记录排队中的因子(它们在哪台机的 sidecar,搬运时留意)
uv run ops status --status checking | tail -3    # 预期无;有则等它跑完
# 三机各看一眼本机存量:
ls /tank/vault/alphalib.local/staging/ 2>/dev/null           # 160
ls /tank/vault/alphalib.local/staging/ 2>/dev/null           # 150(路径同)
ls /storage/vault/alphalib.local/staging/ 2>/dev/null        # 144

# 1b. 换软链为实目录(任一机执行一次,全局生效;用 root)
cd /tank/vault/alphalib
sudo mkdir staging.jfs
sudo chown root:alpha-core staging.jfs && sudo chmod 2750 staging.jfs   # 与 alpha_src 同款权限
sudo rm staging                                   # 删软链(只删链接对象,不动各机 sidecar 数据)
sudo mv staging.jfs staging
ls -la /tank/vault/alphalib/ | grep staging       # 预期:目录,非软链

# 1c. 各机存量搬进共享目录(有存量的机器逐台;root)
sudo mv /tank/vault/alphalib.local/staging/* /tank/vault/alphalib/staging/ 2>/dev/null   # 160/150
sudo mv /storage/vault/alphalib.local/staging/* /storage/vault/alphalib/staging/ 2>/dev/null  # 144(路径经本机挂载点)
# 同名冲突(两台机器 staging 有同一因子)理论不该有 —— 真撞上:停,报告列出冲突名单

# 1d. 跨机可见性验证
sudo touch /tank/vault/alphalib/staging/.probe            # 160
ls /tank/vault/alphalib/staging/.probe                    # 150 上看
ls /storage/vault/alphalib/staging/.probe                 # 144 上看
ls /ext4/alphalib/staging/.probe                          # 170 上看
sudo rm /tank/vault/alphalib/staging/.probe
stat -f -c %T /tank/vault/alphalib/staging/               # 预期 fuseblk(JFS),不再是 zfs
```

窗口到此结束(1a-1d 全程分钟级)。三机 sidecar 里空掉的 `alphalib.local/staging`
目录留着不动(无害;彻底清理等验证稳定后)。

## 阶段 2 · 金丝雀跨机流转(核心验收)

夹具照 VERIFY-PV7 阶段 0(config.verify.yaml,corr=1.01;dropbox 金丝雀重建)。

```bash
export CANARY=AlphaWbaiCanary001

# 2a. 160 submit(入队)
uv run ops submit -u wbai -s $(date +%Y%m%d) -f $CANARY        # 160 上
ls /ext4/alphalib/staging/$CANARY/meta.json                    # 170 上立即可见

# 2b. 170 消费(第一次跨机 check;首跑单因子,观察 /ext4 IO 对 clickhouse 的影响)
uv run ops check -f $CANARY -c config.verify.yaml              # 170 上
# 预期 7 stage 全过 → lib

# 2c. 160 看结果(队列语义闭环)
uv run ops status $CANARY                                      # 160 上;预期 active
ls /tank/vault/alphalib/alpha_src/$CANARY/                     # 共享归档可见
ls /tank/vault/alphalib/pnl_manual/$CANARY                     # 池副本可见
ls /ext4/alphalib.local/alpha_dump/$CANARY | head -3           # 170 上;dump 落消费机 sidecar(设计如此)

# 2d. restage 回环(异机发起召回 → 170 再消费)
uv run ops restage $CANARY -y                                  # 160 上(召回进共享 staging)
uv run ops check -f $CANARY -c config.verify.yaml              # 170 上;再过 → active

# 2e. 清理
uv run ops rm $CANARY -y                                       # 任一机
# 三表零行 + 盘面零残留核对(SELECT + ls,同 SMALLS 手册 4c 格式)
rm -f config.verify.yaml && rm -rf /mnt/storage/dropbox/wbai/$(date +%Y%m%d)/$CANARY
```

## 阶段 3 · 报告

写入 `docs/remediation/DEPLOY-SHARED-STAGING-RESULT.md`,push 到
`claude/shared-staging-queue` 分支:阶段 0 冒烟原文、1d 四机可见性 + stat 原文、
2b check 汇总行、2c 各落点 ls 原文、2e 零残留核对。**文档更新(CLAUDE.md 的
staging 共享事实、config 注释)由判读方在验证全绿后同批改**,执行者不动文档。
任何一步不符:停在那一步。
