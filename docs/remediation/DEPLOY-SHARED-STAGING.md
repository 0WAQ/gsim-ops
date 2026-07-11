# 共享 staging + 170 消费机部署手册(v2,2026-07-11 重写)

**目标**:落地 `docs/shared-staging-queue.md` —— staging 从本机 sidecar 迁 JFS
共享,之后任意机器 submit,170 跑 check,任意机器看结果。

**v2 变更**:原 v1(e314695)写于 170 迁移和 ops setup 之前。现状已变:
- **阶段 0(170 部署)已由 ops setup 工程完成**(MIGRATE-170-RESULT:挂载
  /nvme125/alphalib、PG 通、gsim 在位、`setup --check` FAIL 0),本手册只剩小项;
- 本分支代码 `STAGING_IS_SHARED = True`(staging 应然 = JFS 实目录),
  `ops setup` 直接参与切换(建目录 + 权限);
- 170 路径全部按 /nvme125。

**红线**:
1. 阶段 1 须**短窗口**(分钟级):开始前确认三机(160/150/144)无 in-flight
   submit/check/restage/cancel/clear,窗口内禁这些命令;
2. 金丝雀写操作只限 `AlphaWbaiCanary001`;
3. 每步贴原文;不符即停;不碰 alpha_src/alpha_pnl/alpha_feature 存量;
   /nvme125 四个既有 dataset(alpha_dump/alpha_pnl/checkpoint/datasvc)不碰。

---

## 阶段 0 · 剩余小项(无窗口,160 与 170)

```bash
# 两机:本分支同步(160 拉 GitHub;170 经 160 直推)+ 170 重装 ops
#   ~/.local/bin/uv tool install --reinstall ~/gsim-ops
# 170:gsim 工作区目录(check 的 pnl/alpha/checkpoint 工作路径;config workspace=/home/wbai/alpha)
mkdir -p ~/alpha/dropbox/{pnl,alpha,checkpoint}
# 170:金丝雀 check 用的宽松阈值 config(照 VERIFY-PV7 阶段 0 的 config.verify.yaml snippet,corr=1.01)
# 160:dropbox 金丝雀重建(照 VERIFY-PV7 阶段 0;重建前 rm -rf 旧目录)
```

## 阶段 1 · staging 切共享(短窗口;软链删除一次全局生效 + 各机搬存量)

**事实基础**:挂载点内的 `staging` 软链是单一 JFS 对象(相对 target 逃出挂载
点落各机本地)—— 删软链一次全局生效;**各机 sidecar 里的存量**要逐机搬。

```bash
# 1a. 窗口确认(160)
uv run ops status --status checking | tail -3     # 预期无;有则等
uv run ops status --status submitted | tail -3    # 记录排队因子(在哪台 sidecar,1c 留意)
# 三机各看本机 sidecar 存量:
ls /tank/vault/alphalib.local/staging/ 2>/dev/null        # 160 / 150(路径同)
ls /storage/vault/alphalib.local/staging/ 2>/dev/null     # 144
ls /nvme125/alphalib.local/staging/ 2>/dev/null           # 170(刚迁,预期空)

# 1b. 软链换实目录(160 上,分支代码;一次全局生效)
sudo rm /tank/vault/alphalib/staging                      # 删共享软链(不动各机 sidecar 数据)
uv run ops setup                                          # STAGING_IS_SHARED=True:建 staging 实目录 + root:alpha-core 2750
ls -la /tank/vault/alphalib/ | grep staging               # 预期:目录,非软链
uv run ops setup --check                                  # 预期 FAIL 0

# 1c. 各机 sidecar 存量搬进共享目录(有存量的机器逐台;root)
sudo mv /tank/vault/alphalib.local/staging/* /tank/vault/alphalib/staging/ 2>/dev/null    # 160/150
sudo mv /storage/vault/alphalib.local/staging/* /storage/vault/alphalib/staging/ 2>/dev/null   # 144
# 同名冲突理论不该有;真撞上:停,报告冲突名单

# 1d. 四机可见性 + 类型验证
sudo touch /tank/vault/alphalib/staging/.probe            # 160
ls /tank/vault/alphalib/staging/.probe                    # 150
ls /storage/vault/alphalib/staging/.probe                 # 144
ls /nvme125/alphalib/staging/.probe                       # 170
sudo rm /tank/vault/alphalib/staging/.probe
stat -f -c %T /tank/vault/alphalib/staging/               # 预期 fuseblk(JFS),不再 zfs
```

窗口到此结束。各机搬空的 `alphalib.local/staging` 目录留着(无害,稳定后清)。

## 阶段 2 · 金丝雀跨机流转(核心验收)

```bash
export CANARY=AlphaWbaiCanary001

# 2a. 160 submit(入队)
uv run ops submit -u wbai -s $(date +%Y%m%d) -f $CANARY            # 160
ls /nvme125/alphalib/staging/$CANARY/meta.json                     # 170 立即可见

# 2b. 170 消费(首个跨机 check;cd ~/gsim-ops 再跑)
~/.local/bin/ops check -f $CANARY -c config.verify.yaml           # 170;预期 7 stage 全过 → lib

# 2c. 160 看结果(队列语义闭环)
uv run ops status $CANARY                                          # 160;预期 active
ls /tank/vault/alphalib/alpha_src/$CANARY/                         # 共享归档可见
ls /tank/vault/alphalib/pnl_manual/$CANARY                         # 池副本可见
ls /nvme125/alphalib.local/alpha_dump/$CANARY | head -3            # 170;dump 落消费机 sidecar(设计如此)

# 2d. 异机召回回环(160 restage → 170 再消费)
uv run ops restage $CANARY -y                                      # 160(召回进共享 staging)
~/.local/bin/ops check -f $CANARY -c config.verify.yaml           # 170;再过 → active

# 2e. 清理
uv run ops rm $CANARY -y                                           # 任一机
# 三表零行 + 盘面零残留核对(SELECT + ls,照 SMALLS 手册 4c 格式)
# 170: rm -f config.verify.yaml;160: rm -rf /mnt/storage/dropbox/wbai/$(date +%Y%m%d)/$CANARY
uv run ops list 2>/dev/null | tail -1                              # Total 回基线
```

## 阶段 3 · 报告

写入 `DEPLOY-SHARED-STAGING-RESULT.md` push 回本分支:1a 存量清单、1b 的
setup 输出与 --check 汇总行、1d 四机 probe + stat 原文、2b check 汇总行、
2c 各落点 ls 原文、2e 零残留核对。全绿后判读方合 main(本分支文档已按共享后
事实写好),三机(160/150/144)滚存 main 后各跑 `ops setup --check` 收尾。
任何一步不符:停在那一步。
