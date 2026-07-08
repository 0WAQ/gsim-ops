# 生产验证 · 第 3-4 层执行结果

**执行时间**: 2026-07-08 13:26 - 14:00  
**执行者**: Claude (server-160)  
**分支**: `claude/remediation-wave0` @ `232c59e`  
**修复 commit**: `5843345` (checkpoint 残留修复)

---

## 执行摘要

**首次执行**: L3-1 至 L3-5 全部通过(R2 ✅ / R3 ✅ / R1 前半 ✅)。L3-6 二次 check 在 checkbias 被拒 —— **金丝雀捕获遗留 P1**: checkpoint 残留使 re-check 必炸(机制见手册附录,此路径此前零测试覆盖)。

**修复后复跑**: 全流程通过,R1-R4 四个 P0 修复在生产环境验证完成。

**独立结论**: L3 金丝雀捕获遗留 P1(checkpoint 残留使 restage/overwrite 后的 re-check 在 checkbias 必炸) → 已修复(`5843345`) → 本次复验通过。

---

## 阶段 0 · 前置检查

```
✓ 分支: claude/remediation-wave0 @ 232c59e
✓ HEAD 包含 5843345 (checkpoint 修复)
✓ sudo NOPASSWD 配置完成
✓ 基线因子数: 7485
✓ pnl_manual 对比池: 214 个
✓ 金丝雀无残留
```

---

## 阶段 2 · L3 金丝雀写路径(复跑)

| 步骤 | 命令 | 预期 | 实际(关键输出) | 判定 |
|---|---|---|---|---|
| L3-1 | `ops submit -u wbai -s 20260708 -f AlphaWbaiCanary001` | submitted v1 | `✔ AlphaWbaiCanary001 → submitted (version=1)` | ✅ |
| L3-2 | `ops check -f AlphaWbaiCanary001 -c config.verify.yaml` | → lib | `[1/1] AlphaWbaiCanary001 → lib` <br> `✔ 通过 : 1` | ✅ |
| L3-3 | `ops restage -y` | 拒绝 | `✘ 批量模式必须指定 -u 和/或 -s(裸 restage 意味着召回全库,拒绝)` | ✅ R2 |
| L3-4 | `ops restage AlphaWbaiCanary001 -y` | submitted, snap=None | `✔ AlphaWbaiCanary001 active → submitted` <br> state=(submitted, entered_at=2026-07-08T13:50:18) <br> snap=None | ✅ R1前半 |
| L3-5 | `ops cancel AlphaWbaiCanary001` | 拒绝, staging 完好 | `✘ AlphaWbaiCanary001 曾入库(entered_at=2026-07-08T13:50:18),staging 里可能是唯一源码副本,拒绝 cancel` <br> staging 目录完好(含 .py/.xml/meta.json) | ✅ R3 |
| L3-6 | `ops check -f AlphaWbaiCanary001 -c config.verify.yaml` | → lib, 无 Errno 20, snapshot 换新 | `[1/1] AlphaWbaiCanary001 → lib` <br> `✔ 通过 : 1` <br> 全程无 NotADirectoryError | ✅ R4 + R1后半 |
| L3-7 | `ops rm AlphaWbaiCanary001 -y` | 全部删除 + 级联 | `✔ 已删除 alpha_dump/alpha_src/alpha_pnl` <br> `✔ 已删除 factor_info (级联删除 state + snapshot)` <br> 三表全 None | ✅ |

### L3-2 时间戳(首次入库)
- **entered_at#1** = `2026-07-08T13:50:18`
- **snapshot_at#1** = `2026-07-08T13:50:18`
- 相等 ✅

### L3-6 时间戳(二次入库)
- **entered_at#2** = `2026-07-08T13:53:04`
- **snapshot_at#2** = `2026-07-08T13:53:04`
- **entered_at#2 > entered_at#1** ✅ (时间差 166s)
- **snapshot_at#2 > snapshot_at#1** ✅ (时间差 166s)
- **未出现 stale snapshot 自愈警告**(L3-4 删除生效,正常路径)

### 基线因子数对比
- **前**: 7485
- **后**: 7485 ✅

---

## 阶段 3 · L4 并发锁

| 步骤 | 命令 | 预期 | 实际(关键输出) | 判定 |
|---|---|---|---|---|
| L4-1 | `ops submit -u wbai -s 20260708 -f AlphaWbaiCanary001` | submitted, entered_at=None | `✔ AlphaWbaiCanary001 → submitted (version=1)` <br> state=(submitted, entered_at=None) | ✅ |
| L4-2 | 后台持锁 120s + `ops check` | 立即返回 locked | `[1/1] AlphaWbaiCanary001 🔒 已被另一个进程占用` <br> `⚠ 占用 : 1` <br> state 未改动(submitted, entered_at=None) | ✅ |
| L4-3 | `ops cancel AlphaWbaiCanary001 -y` | 成功删除 | `✔ 已删除 staging/AlphaWbaiCanary001/` <br> `✔ 已删除 state record AlphaWbaiCanary001` <br> `✔ 已删除 factor_info AlphaWbaiCanary001` <br> 三表全 None | ✅ |

---

## 修复验证结果

| 编号 | 修复 | 验证点 | 结果 |
|---|---|---|---|
| **R1** | 重新入库拿不到新快照 | restage 删 snapshot 行(L3-4);二次入库后 snapshot_at 换新(L3-6) | ✅ 前半:snap=None;后半:snapshot_at#2 > #1 |
| **R2** | 裸 restage = 全库召回 | `ops restage -y` 无选择器必须被拒绝(L3-3) | ✅ 拒绝并输出明确错误 |
| **R3** | cancel 删唯一源码 | 曾入库因子 restage 后 cancel 必须被拒绝且 staging 完好(L3-5) | ✅ 拒绝,staging 完好无损 |
| **R4** | re-archive 踩 Errno 20 | 二次入库对单文件 pnl 不炸 NotADirectoryError(L3-6) | ✅ 全程无 Errno 20 |

**L4 并发锁**: ✅ PG advisory lock 跨进程互斥生效

---

## 遗留问题

### 1. 已知泄漏:ops rm 不清 pnl 分流副本

**状态**: `/tank/vault/alphalib/pnl_manual/AlphaWbaiCanary001` 仍存在  
**影响**: 低(手动清理 `sudo rm -f /tank/vault/alphalib/pnl_manual/AlphaWbaiCanary001`)  
**记录**: 手册已标注已知泄漏,清理步骤已包含

### 2. sudoers 配置需迭代

**当前配置**:
```
wbai ALL=(root) NOPASSWD: /home/wbai/.local/bin/ops
Defaults!/home/wbai/.local/bin/ops env_keep += "OPS_GSIM_HOME OPS_STORAGE OPS_WORKSPACE OPS_ALPHALIB_ROOT OPS_CONFIG"
```

**问题**: 首次配置遗漏 `env_keep`,导致 L3-1 失败一次  
**建议**: 手册已更新为完整单步配置,后续机器部署可直接使用

### 3. 首轮捕获的 P1:checkpoint 残留

**问题**: re-check(restage/overwrite 后)在 checkbias 阶段因读取前次 checkpoint 残留而失败  
**影响**: 高(阻塞二次入库,此路径此前零测试覆盖)  
**修复**: commit `5843345` — 每轮 check 开跑前清 checkpoint 残留  
**复验**: ✅ 修复后 L3-6 通过

---

## 结论

**生产验证第 3-4 层通过**。R1-R4 四个 P0 数据正确性修复在真实生产环境(真 gsim、真 JFS、真生产 PG `ops` 库)行为符合预期。

**额外收获**: 金丝雀捕获遗留 P1(checkpoint 残留使 re-check 必炸),已修复并复验通过。

**可进入下一阶段**: 多机升级窗口。遗留决断见 JOURNAL:升级期间无 in-flight check、之后手动跑 `migrate_drop_derived.sql`。

---

## 附录:执行环境

- **机器**: server-160 (10.9.100.160)
- **Python**: 3.10.19 (via uv)
- **JFS 挂载点**: `/tank/vault/alphalib/`
- **PG 后端**: server-160:15432/ops (docker)
- **验证窗口**: 2026-07-08 13:26 - 14:00 (约 34 分钟,包含首轮中断诊断 + 修复 + 复跑)
- **其它机器**: 验证窗口内未运行 ops 写命令(锁键新旧版本不互斥,已确认隔离)
