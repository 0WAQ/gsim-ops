# 多机升级窗口执行结果 · server-150 / intel-workstation-144

**执行手册**: `docs/remediation/VERIFY-UPGRADE-150-144.md`
**协调机**: server-160 (10.9.100.160)，执行者 Claude Code
**执行日期**: 2026-07-08

> **状态: 窗口未开启 — 阻塞在阶段 0a。** 阶段 1-5 均未执行。
> 阶段 0 前置探测在 144 发现 in-flight `ops pack`（红线 1 违规），触发事件处置流程；
> 事件已定性并部分收尾，但暴露一起需 wbai 评估的**生产 JFS feature 覆盖事件**，窗口保持关闭。

---

## 阶段 0 · 全局前置

### 0a 三机 in-flight / cron 检查

| 机器 | in-flight ops | cron | 判定 |
|---|---|---|---|
| server-160 | NO-INFLIGHT | NO-CRON | ✅ 干净 |
| server-150 | NO-INFLIGHT | NO-CRON | ✅ 干净 |
| **intel-144** | **20 × `ops pack` (root, ppid=1)** | NO-CRON | ❌ **红线 1 违规 — 停止** |

**判定: 阶段 0a 未通过，窗口不得开启。** 按手册"实际与预期不符立即停止不自行修复"，转入事件处置。

### 0b 160 基线（仅供后续对照，窗口未实际开启）

- 部署 rev: `5fbbe16`（`docs(remediation): 150/144 多机升级窗口执行手册`）
- `uv run ops list` Total: **7488 factors**
- 150 / 144 升级前旧 rev: 均为 `6225b7b`（`feat(check): route pnl by discovery_method`）

---

## 事件 · 144 孤儿 `ops pack` + 生产 JFS feature 覆盖

### E1 现象

144 上 20 个 `ops pack` worker 进程，`ppid=1`（父进程已退出，被 reparent 到 init），
`etime` 约 **7 天 9 小时**（启动 ≈ 2026-07-01）。两批启动时间 `7-09:23:04` / `7-09:23:28`。

### E2 取证（只读，判定为空转僵尸）

| 核实项 | 命令/方法 | 结果 |
|---|---|---|
| ① cputime 冻结 | 间隔 60s 两次采样 `ps -o time=` | 20 个 worker TIME 值**一字不差**，最大 14s / 7 天 → 阻塞在 ProcessPool 队列读，零推进 |
| ② 无写句柄 | `ls /proc/$p/fd` 过滤 pipe/socket | 仅 pipe/socket，**无任何指向 alpha_feature 的写句柄** |
| ③ 启动取证 | `readlink /proc/$p/cwd` + `cmdline` | cwd=`/home/wbai`；cmdline=**裸 `ops pack`，未带 `-c`** |
| 属主 | `/proc/$p/status` Uid | **Uid=0（root）** — 旧版 ops self-elevate sudo 提权写盘所致；wbai 无权 kill |

**定性**: 空转僵尸。符合 wbai 代码侧判定（旧版 ProcessPoolExecutor，父死后 worker 永久阻塞队列读；
`_atomic_write_memmap` tmp+os.replace 原子写，最坏只留 `.tmp` 残渣，目标文件不会半成品）。

### E3 清理

- kill 需 root（144 上 `sudo -n` 需密码，协调机无法提供），由 **wbai 本人**在 144 执行 `sudo pkill`。
- **kill 确认干净**: 精确匹配 `python3 .../ops pack`，无 STILL-ALIVE 残留。✅

### E4 善后 — 写到哪棵树（核心发现）

| 树 | 路径 | 结果 |
|---|---|---|
| 幽灵树（本地盘） | `/tank/vault/alphalib`（144 上） | **不存在** ✅ — 本次 pack **未**造幽灵树 |
| 共享 JFS | `/storage/vault/alphalib`（144 挂载点） | `fuse.juicefs` `JuiceFS:alphalib` — **写入这里** |
| 旧 prod 软链 | `/mnt/storage/alphalib` → `/storage/vault/alphalib` | 同一棵树（软链），不是独立副本 |

**⚠️ 本次 pack 写进了共享 JFS 生产卷，不是 144 本地。** 交叉验证（160 视角，同一 JFS 卷挂载点 `/tank/vault`）：

- 160 上 `alpha_feature` 7/1 窗口写入数 = **5757**，与 144 视角完全一致
- 抽查同名文件 mtime = **2026-07-01 10:00~10:28**，与 144 pack 启动时间（≈7/1）吻合
- 写入 mtime 范围: **2026-07-01 09:52 → 2026-07-02 12:53**（跨约 27 小时后父进程死亡，worker 转空转）

**结论**: 7/1 在 144 上跑的 `ops pack`（无 `-c`，走默认 config）经共享 JFS 覆盖了 **5757 个生产 alpha_feature**，
160 当前读到的就是这批。命中 wbai 预警的"写进共享 JFS → 停下评估"结局。

### E5 受影响因子分布（5757 个，160 视角）

| 作者前缀 | 数量 |
|---|---|
| AlphaFguo | 4454 |
| AlphaHwang | 1128 |
| AlphaYbai | 116 |
| AlphaZxu | 56 |

**大部分是他人因子（Fguo/Hwang 占 97%），非 wbai。** 若 144 用 stale 冷副本 alpha_dump 打包，
则这些他人因子的生产 feature 可能被过期数据覆盖 —— 待评估。

### E6 tmp 写残渣（JFS 上，160/144 同一份）

| 文件 | size | mtime |
|---|---|---|
| `.AlphaZxu_260621_PxRangePos_delay1.v1.npy.tmp` | 171100800 | **2026-06-23 22:09** ← 更早，非本次 pack |
| `.AlphaYbai0615TaxTruth.v1.npy.tmp` | 171100800 | 2026-07-01 09:53 |
| `.AlphaYbai0615ValueRankEPTTMQ4TaxResid.v1.npy.tmp` | 171100800 | 2026-07-01 09:53 |

后两个是本次 kill 时死在写入中途的半成品；第一个（Zxu PxRangePos）mtime 06-23，是**更早一次**未收尾的残渣。
三个均未清理，待处置。

---

## 待 wbai 评估 / 决策（窗口保持关闭）

1. **生产 feature 覆盖评估（阻塞项）**: 144 的 7/1 pack 是否用 stale alpha_dump 打包并污染了 5757 个生产 feature？
   - 待选只读核对: 导出 5757 因子 mtime 清单；抽查 alpha_dump 在 144 vs 160 是否一致（判 stale）；
     确认 Fguo/Hwang 因子的 dump 来源。
   - 若判定污染 → 定重 pack 方案（该在 IDC 机器带正确 config 跑，不在 144 冷副本）。
2. **3 个 tmp 残渣清理**（root 权限，wbai 执行）。
3. 评估/处置结论明确后，从阶段 0a **重新探测开窗**，150 先行。

## 尚未执行

阶段 1（150 升级）、阶段 2（144 升级）、阶段 3（跨机锁验证）、阶段 4（migration）、阶段 5（完整报告）
—— 全部未开始，等窗口重新开启。
