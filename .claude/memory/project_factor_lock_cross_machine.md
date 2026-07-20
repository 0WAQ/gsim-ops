---
name: project-factor-lock-cross-machine
description: factor_lock 从 per-machine fcntl 迁到跨机 PG advisory lock (CHECKING 跨机互斥)
metadata: 
  node_type: memory
  type: project
  originSessionId: 02d7c1d1-953d-4031-8f88-5db321590f79
---

2026-07-04 (branch `feat/derived-postgres`): 把 `ops/infra/lock.py` 的 `factor_lock` 从 per-machine fcntl 文件锁迁到**跨机 PostgreSQL session-level advisory lock**。

**为什么**:state 已迁共享 PG、staging 在共享 JFS,160/150/144 三机都能 `ops check` 扫同一 staging。(⚠ 2026-07-11 校正:staging 实为挂载点下软链落本机 sidecar,**不共享**,"三机扫同一 staging" 不成立。⚠ 再校正 2026-07-20:staging 现已是 JFS 共享实目录(共享 staging 部署已落地),"三机扫同一 staging" 又成立;跨机锁的必要性自始至终不变 —— 别机 restage/rm/approve 仍会与本机 check 撞同一因子的共享 state/产物。)但 factor_lock 一直是 `~/.cache/ops/locks/*.lock` 的 fcntl 锁(per-machine,锁文件各机独立不同步)→ **跨机并发 check 同一因子时本机锁挡不住**:两台都进 `_run_one_locked`、都 transition(CHECKING)、都跑回测、都 to_lib、都写 metrics。CHECKING 状态此前只是被动记录,没人读它拦别的机器。成因:迁 PG 的是 state(数据),lock(并发控制)不在迁移范围,一直是 fcntl。

**方案**:`factor_lock(name)` → `factor_lock(name, config)`,按 `config.state_backend` 分支:
- postgres:专用连接(**非 state pool** —— session advisory lock 必须同连接 acquire/release 全程持有,池会把还持锁的连接给下一个用户)跑 `pg_try_advisory_lock(hashtext(lib), hashtext(name))`,非阻塞返回 bool,False → FactorLocked。session 级锁**连接断开自动释放**(机器崩溃/SIGKILL/断电)→ 天然无死锁残留,故意不用"CHECKING 状态位当锁"(那要加 checking_at 时间戳字段 + 处理 stale 抢占,复杂且刚清理过 schema)。
- json:回退原 fcntl,完全不变;postgres 无 conninfo = 硬错误拒绝静默降级 (2026-07 起;redis 后端已删,该分支不复存在)。

**9 个调用点**加 config 实参:check/submit/rm/cancel/clear/approve/restage/run/pack。pack 的 `_pack_worker`(ProcessPool worker)原本无 config,给它加了 config 参数 + pool.submit 传参。

**subagent 审 bug 结论**:整体正确无高危。采纳 1 修:unlock SQL 包 try/except(PG 抖动时 unlock 失败不该遮盖成功的临界区,锁靠 conn.close 兜底释放)。未改但记录:check 的 run_one 只 catch FactorLocked,PG 连接故障会走 _run_one_locked 外层 catch-all Exception 回退 SUBMITTED(归 error,可接受——连不上 PG 本就是环境问题)。

**验证**:advisory 跨连接可见(A 持有 B False,A close 后 B True)、ProcessPool 4 worker 竞争恰 1 得 3 locked、json 回退走 fcntl、PG max_connections=100 用 8 余量足。

关联 [[project_factor_library_storage_architecture]] [[project_cli_command_redesign]]。fcntl 锁本身仍是 json/redis 回退路径,不可全删。