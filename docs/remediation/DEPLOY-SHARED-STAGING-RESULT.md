# 共享 staging + 170 消费机部署 · 执行结果(2026-07-11)

对应手册 `DEPLOY-SHARED-STAGING.md` v2。分支 `claude/shared-staging-queue` @ `0b5b358`
(`STAGING_IS_SHARED = True`)。160 拉 GitHub,170 直连 GitHub fetch(MIGRATE-170
记录的"无 GitHub 出口"现已通,直接 checkout,未走 160 SSH 直推)。

**结论:全绿。** staging 从各机 sidecar 软链切为 JFS 共享实目录;160 submit → 170
跨机 check → 任意机看结果的队列语义端到端闭环;异机召回 (160 restage → 170 check)
回环完整。1 处已知盲区记录在末尾(跨机 dump sidecar 清理,doctor 候选)。

---

## 阶段 0 · 剩余小项(无窗口)

- **170 本分支同步 + ops 重装**:`git checkout claude/shared-staging-queue`
  @ `0b5b358`(与 160 一致);`uv tool install --reinstall ~/gsim-ops` →
  `Installed 1 executable: ops`。
- **170 gsim 工作区目录**:`mkdir -p ~/alpha/dropbox/{pnl,alpha,checkpoint}`,
  三目录在位(config `workspace=/home/wbai/alpha`)。
- **170 config.verify.yaml**(corr=1.01,宽松业绩门槛,照 VERIFY-PV7 阶段 0 非 pv7 版):
  `{'ret%': 1.0, 'shrp': 0.1, 'tvr_d0%': 500.0, 'tvr_d1%': 500.0, 'corr_threshold': 1.01}`。
- **160 dropbox 金丝雀重建**:`/mnt/storage/dropbox/wbai/20260711/AlphaWbaiCanary001/`
  = `{AlphaWbaiCanary001.py, Config.AlphaWbaiCanary001.xml}`(复用 e2e `good` 模板)。
  重建前金丝雀名下零残留(盘 + PG 均无)。

## 阶段 1 · staging 切共享(短窗口)

### 1a 窗口确认 + 存量清单

- `checking` 状态:**0**(无 in-flight check)。
- `submitted` 状态:**167 个**(全在三机 sidecar staging,无一属金丝雀):
  - 160: 61(fguo / hwang / xmf)
  - 150: 37(lhw)
  - 144: 69(cchang / sli / ybai)
  - 170: 0(刚迁移,sidecar 空)
- 三机 staging 目录数 (61+37+69=167) = PG submitted 数 (167)。
- **跨机零同名冲突**(160∩150 / 160∩144 / 150∩144 三组 comm -12 均空)。

> 用户决策:167 个 backlog 照手册全量搬迁(解除"金丝雀之外零写"红线覆盖此步)。

### 1b 软链换实目录(160,一次全局生效)

切换前 `setup --check` 基线:唯一 FAIL = `staging 形态: 是软链(应为 JFS 实目录)`,
其余 13 项全绿。

删软链前快照:`/tank/vault/alphalib/staging -> ../alphalib.local/staging`
(相对 target,逃出挂载点落本地盘);挂载点根已 `fuseblk`。

```
sudo rm /tank/vault/alphalib/staging          # 软链删除(160 sidecar 存量 61 未动)
uv run ops setup                              # 已补建: 2(staging 实目录 + 顶层权限)
```

`ops setup` 输出:`staging 形态 → 全部为实目录(已补建)`,`FAIL: 0 WARN: 0 已补建: 2`。
实目录形态:`drwxr-s--- root alpha-core`(2750 setgid),`stat -f = fuseblk`。
`setup --check` 复检:`FAIL: 0`。

### 1c 各机 sidecar 存量搬入共享目录

三机各一条 `sudo mv <sidecar>/staging/* <shared>/staging/`(160/150 `/tank/vault`,
144 `/storage/vault`)。搬后:

- 共享 staging = **167** 个;三机 sidecar staging 全 = **0**。
- `diff` 校验:共享目录 167 个 = 三机原始并集(167),**零丢失零覆盖**。

### 1d 四机可见性 + 类型验证(只读法:167 真因子跨机一致)

用 160 submit 的 `AlphaFguo20260701GA043` 做可见性探针(比 .probe 更强,验真实因子):

| 机器 | 挂载点 | 探针可见 | count | fstype |
|---|---|---|---|---|
| 160 | /tank/vault/alphalib/staging | ✔ | (本机) | fuseblk |
| 150 | /tank/vault/alphalib/staging | ✔ | 167 | fuseblk |
| 144 | /storage/vault/alphalib/staging | ✔ | 167 | fuseblk |
| 170 | /nvme125/alphalib/staging | ✔ | 167 | fuseblk |

四机 count 一致、同因子可见、全 fuseblk(不再 zfs/ext4 sidecar)→ **软链删除全局
生效 + 共享实目录跨机一致** 确认。窗口结束(孤儿窗口关闭:存量已全入共享)。

## 阶段 2 · 金丝雀跨机流转(核心验收)

### 2a 160 submit(入队)

`AlphaWbaiCanary001 → submitted (version=1)`(XML auto-fix: PLACEHOLDER → 因子名)。
金丝雀落共享 staging(`root:alpha-core`),**170 立即可见** meta.json(`170-SEES-META-OK`)。

### 2b 170 消费(首个跨机 check)

`cd ~/gsim-ops && ~/.local/bin/ops check -f AlphaWbaiCanary001 -c config.verify.yaml`
(170 无 NOPASSWD,由 wbai 在 170 终端跑,check self-elevate)。
结果:**7 stage 全过 → 正常入库**(用户确认)。

### 2c 160 看结果(队列语义闭环)

| 落点 | 位置 | 结果 |
|---|---|---|
| 状态 | PG | `active`,entered_at=19:26:39 |
| alpha_src/ | 共享 JFS | ✔ 可见 |
| alpha_pnl(单文件) | 共享 JFS | ✔ 365328 bytes root:alpha-data |
| pnl_manual(池副本) | 共享 JFS | ✔ 365328 bytes(discovery=manual) |
| alpha_dump/ | **170 sidecar** `/nvme125/alphalib.local/alpha_dump/` | ✔ 11 年份目录(消费机本地,不进 JFS) |

对照:160 共享 alpha_dump 下**无**金丝雀实体(160 alpha_dump 软链 → 160 本机
sidecar,每机各一份)→ 印证"谁 check 谁在本机产 dump,src/pnl/池副本共享"。

### 2d 异机召回回环(160 restage → 170 再 check)

- 160 `restage -y`:`active → submitted`,回收 `alpha_pnl` + `pnl_manual`(check 面),
  dump 保留(服务面 last-known-good)。
- 召回后 170 立即可见 meta.json(`170-SEES-RECALLED-OK`);共享 pnl/池副本已清空。
- 170 二次 check(用户在 170 终端跑):**再次全过 → active**,新 entered_at=19:29:26,
  alpha_pnl + 池副本重建。

### 2e 清理

`ops rm -y`(160):删共享 src/pnl/池副本 + PG 三表(级联 state+snapshot)。
核对:共享落点零残留、PG `未找到因子`、160 dropbox + 报告清空、基线 `Total: 8252`。

**⚠ 已知盲区(doctor 候选)**:`ops rm` 从 160 跑,alpha_dump 软链指 160 本机
sidecar,**清不到 170 sidecar 的 dump 实体**(`/nvme125/alphalib.local/alpha_dump/
AlphaWbaiCanary001` 遗留)。共享 staging 架构下,跨机 check 产的 dump 留在消费机
sidecar,rm 只清本机侧。本次手工 `sudo rm -rf` 在 170 清除。**根治留给未来
ops doctor**(盘 ↔ PG 对账 + 跨机 sidecar 回收)。清后 170 彻底干净。

---

## 收尾建议

全绿。判读方可合 main(本分支文档已按共享后事实写好)。合 main 后三机
(160/150/144)+ 170 滚存各跑 `ops setup --check` 收尾(预期 staging 形态项全绿)。

**doctor backlog 新增一条**:跨机 dump sidecar 回收 —— PG 不记因子躺哪台机
staging / 产 dump 在哪台机 sidecar,rm/purge 只作用本机;需 doctor 做盘 ↔ PG
跨机对账。
