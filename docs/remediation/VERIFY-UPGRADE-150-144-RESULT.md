# 多机升级窗口执行结果 · server-150 / intel-workstation-144

**执行手册**: `docs/remediation/VERIFY-UPGRADE-150-144.md`
**协调机**: server-160 (10.9.100.160)，执行者 Claude Code
**执行日期**: 2026-07-08

> **状态: 全部阶段完成 ✅** 三机(160/150/144)升级至 rev `7f5b710`，跨机 PG advisory
> 锁互斥四观测全符，僵尸表 migration 执行成功。窗口已解除。
>
> 开窗前经历一次阻塞:阶段 0a 在 144 发现 20 个孤儿 `ops pack`（7 天前父进程死亡的空转
> 僵尸），wbai 核实后清理，并确认其 7/1 经共享 JFS 覆盖的 5757 个 feature **无污染
> (本人操作、dump 等价)**，事件结案(commit `7f5b710`)后重新开窗。

---

## 阶段 0 · 全局前置

### 0a 三机 in-flight / cron 检查

第一次探测(开窗被阻塞):

| 机器 | in-flight ops | cron | 判定 |
|---|---|---|---|
| server-160 | NO-INFLIGHT | NO-CRON | ✅ |
| server-150 | NO-INFLIGHT | NO-CRON | ✅ |
| intel-144 | **20 × `ops pack` (root, ppid=1)** | NO-CRON | ❌ 阻塞 |

事件处置后(见文末"事件"节)重新探测:三机全 NO-INFLIGHT / NO-CRON ✅，窗口开启。

### 0b 160 基线

- 部署 rev: `7f5b710`（含 144 pack 事件结案 commit）
- `ops list` Total: **7488 factors**
- 150 / 144 升级前旧 rev: 均 `6225b7b`（`feat(check): route pnl by discovery_method`），
  均在 `main` 分支

---

## 阶段 1 · server-150 升级 ✅

| 步骤 | 结果 |
|---|---|
| 1-1 环境探测 | server-150 / 10.9.100.150；旧 rev `6225b7b`；uv 0.9.26；JFS `/tank/vault/alphalib` 挂载正常；gsim 存在；DATASVC-OK |
| 1-2 代码升级 | 工作区干净；checkout+pull `claude/remediation-wave0` → rev **`7f5b710`**（与 160 一致）；`uv sync --group dev` 成功（去 boto3/tqdm，加 tomli） |
| 1-3 PG 密码 | `.env` 缺失（gitignore），从 160 scp 分发，chmod 600 ✅（未 cat） |
| 1-4 装 ops + sudoers | `uv tool install` → `/home/wbai/.local/bin/ops`；sudoers 由 wbai 本机配置后 `NOPASSWD-OK`（文件 440 root:root） |
| 1-5 L1 测试 | **51 passed / 8 skipped / 0 failed**，5.65s（干净串行重跑；见下"测试串行化"注） |
| 1-6 只读冒烟 | `list` Total **7488**（三机同 PG，验证点达成）；`list -u wbai` 3 factors；`info` 快照完整；`list --format json` exit 141（BrokenPipe 修复正常） |

**两处非故障坑（已定性）**:
1. 生产入口是 PATH 里的 `ops`（tool install），非 `uv run ops`（项目 venv 未生成 console
   script，`.venv/bin/ops` 不存在）。手册写的 `uv run ops` 在 150/144 不适用。
2. 手册 1-6 的 `ops list --author wbai` 是笔误 —— `list` 的作者 flag 是 `-u/--user`。

---

## 阶段 2 · intel-workstation-144 升级（WAN 节点）✅

差异处理:
- **2-0 挂载点**: `/storage/vault/alphalib`（fuse.juicefs），`/mnt/storage/alphalib` 为其软链，
  无 `/tank` 幽灵路径；`OPS_ALPHALIB_ROOT=/storage/vault/alphalib` 写入 `~/.bashrc`。
- 旧 rev `6225b7b`；uv 0.8.9；有一个 untracked check 报告残留（非 tracked 脏改动，不阻塞）。

| 步骤 | 结果 / WAN 耗时 |
|---|---|
| 代码升级 | checkout+pull → rev **`7f5b710`**；fetch/checkout/pull **20s** |
| `uv sync` | 首次 `UV_HTTP_TIMEOUT=30` 超时（psycopg-binary 下载失败）→ `UV_HTTP_TIMEOUT=180` 重试成功，**约 6min**；psycopg **3.3.4** |
| PG 密码 | 从 160 scp 分发，chmod 600 ✅ |
| 装 ops + sudoers | `/home/wbai/.local/bin/ops`；sudoers wbai 本机配置后 `NOPASSWD-OK` |
| 1-5 L1 测试 | **51 passed / 8 skipped / 0 failed**，**335s (5m35s)**（干净串行；用例数与 160/150 完全一致，仅 WAN 慢约 40×） |
| 1-6 只读冒烟 | `list` Total **7488**（**10s**）；`info` Source 路径正确指向 `/storage/vault/alphalib`（`OPS_ALPHALIB_ROOT` 覆盖生效）；json exit 141 |

**WAN 基线记录**: fetch/pull 20s / uv sync ~6min（需 `UV_HTTP_TIMEOUT=180`）/ L1 335s / list 10s。

---

## 测试串行化事故与纠正（红线注解）

144 首跑 L1 出现 **5 passed / 54 skipped** 异常（与 150 的 51/8 量级不符）。根因排查:
- 非 PG 不可达（`ops_test` 从 144 用 5s timeout 0.7s 即连上）；
- 真相:一个我误判为"ssh 超时失败"、实际仍在 144 后台运行的 pytest（旧 `-k "pg or store or state"`
  命令）**与 150 的重跑撞同一个 `ops_test` 库**（`wipe_test_db` 清库），导致 150 一次重跑出现
  **3 failed**。这正是手册"不得与其它机器同时跑测试"红线。

纠正: kill 掉 144 残留 pytest → 三机确认无 pytest → **严格串行**各跑一次:150 恢复
51/8/0 ✅，144 干净跑出 51/8/0 ✅。

（`test_state_store_pg.py` 的 8 个 skip 是源码显式标记 "PG store fixtures 待重建,
full-review I2"，与环境无关，两机一致。）

---

## 阶段 3 · 跨机 PG advisory 锁验证 ✅

F5 新锁键（`hashtext('ops:factor_lock')` 固定命名空间）下三机首次真互斥。金丝雀名
`AlphaWbaiCanary001`。160 持锁 120s，150/144 期间尝试 + 释放后再尝试:

| 观测 | 结果 | 判定 |
|---|---|---|
| 150 held（160 持锁期间） | `FactorLocked` | ✅ 互斥生效 |
| 144 held（160 持锁期间） | `FactorLocked` | ✅ 互斥生效 |
| 160 | `LOCK HELD` → `RELEASED` | ✅ 正常释放 |
| 150 after（释放后） | `ACQUIRED` | ✅ 无残留 |
| 144 after（释放后） | `ACQUIRED` | ✅ 无残留 |

四观测全符 → 跨机互斥验证通过，**升级窗口解除**。

---

## 阶段 4 · migrate_drop_derived.sql（仅 160，手动）✅

前置:
- a. 三机 rev 全 = `7f5b710`（≥ 85b590e）✅
- b. `factor_snapshot` = **7488** 行（≈ list Total）；僵尸表 `factor_derived` + `derived_meta` 存在 ✅
- c. 备份成功: `dumps/ops-20260708-2056.sql.gz` (688K) ✅（红线 3 满足）

脚本内容确认: `BEGIN; DROP TABLE factor_derived CASCADE; DROP TABLE derived_meta CASCADE; COMMIT;`（无其它动作）。

执行结果:
- `\dt` 后仅剩正规三表 `factor_info` / `factor_snapshot` / `factor_state`，僵尸表消失 ✅
- 三机只读回归: Total 全 **7488**（不变），`info` 正常 → 证明 ops 不再引用僵尸表 ✅
- 收尾: 160 删 1 个 `~/.cache/ops/lib/alphalib-juicefs/derived.json` 残留；150/144 无

---

## 三机最终状态

| 机器 | rev | ops list Total | 备注 |
|---|---|---|---|
| server-160 | `7f5b710` | 7488 | 协调机；migration 已执行 |
| server-150 | `7f5b710` | 7488 | IDC |
| intel-144 | `7f5b710` | 7488 | WAN；`OPS_ALPHALIB_ROOT=/storage/vault/alphalib` |

三机读同一生产 PG（Total 全等）、跨机锁互斥、僵尸表已清。**窗口任务全部达成。**

---

## 事件 · 144 孤儿 `ops pack`（开窗前阻塞，已结案）

### 现象与取证

144 上 20 个 `ops pack` worker，`ppid=1`（父进程已死被 reparent 到 init），etime ≈ 7 天
（启动 ≈ 2026-07-01）。取证判为空转僵尸:
- ① cputime 冻结:60s 两次采样 TIME 值一字不差（最大 14s / 7 天）→ 阻塞在 ProcessPool 队列读；
- ② fd 仅 pipe/socket，无 alpha_feature 写句柄；
- ③ cmdline 裸 `ops pack`（无 `-c`）；属主 **root**（旧版 self-elevate sudo 提权），wbai 无权 kill。

### 清理与善后

- kill 需 root，由 wbai 本机 `sudo pkill` 清理干净 ✅。
- 善后核实写到哪棵树: **无幽灵树**（`/tank/vault/alphalib` 在 144 不存在）；写进了**共享 JFS**
  `/storage/vault/alphalib`。160 交叉验证同一 JFS 卷有 **5757** 个 7/1 窗口写入
  （mtime 2026-07-01 09:52 → 07-02 12:53），同名文件 mtime 吻合。
- 受影响 5757 因子分布: AlphaFguo 4454 / AlphaHwang 1128 / AlphaYbai 116 / AlphaZxu 56。
- tmp 残渣 3 个（其中 `.AlphaZxu_260621_PxRangePos` mtime 06-23，是更早一次的残渣）。

### 结案

wbai 从代码侧核实 + 确认: **本人操作，144 pack 用的 dump 与生产等价，无污染**（commit
`7f5b710`）。事件不再阻塞升级，窗口重新开启。

---

## 遗留（不在本窗口）

- Redis 残留 state key 清理（验稳后，只 DEL state:*，绝不 FLUSHDB）；
- PG 密码正规化（挪 /etc root-only）；
- 3 个 JFS tmp 残渣清理（root 权限）；
- wave3 / stage-table 增量验证与滚存部署；
- 手册两处待订正: `uv run ops` → 直接 `ops`（tool install 入口）、1-6 `--author` → `-u/--user`。
