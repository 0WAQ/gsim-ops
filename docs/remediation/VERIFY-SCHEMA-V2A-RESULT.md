# schema v2a 生产迁移执行结果(2026-07-12,执行者于 160;判读方收录)

五阶段全绿,零意外。手册 `VERIFY-SCHEMA-V2A.md`。

| 阶段 | 内容 | 结果 |
|---|---|---|
| 1 | 同步 + 门禁 | 162 passed, 0 skipped |
| 2 | pg_dump 两表备份 | `/tmp/backup_v2a_202607121940.sql` 3.7M;`dump complete` 标记在,两表 CREATE+COPY 全覆盖 |
| 3 | 只读前置核对 | has_pnl/dump_days 在位(`\d` + COPY 列清单双证);violating_rows = 0 |
| 4 | 两份 SQL | 空档确认(CHECKING 空)后执行;两段均 BEGIN/ALTER×2/COMMIT 零报错 |
| 5 | 复验 | 两列消失、delay 保留;`chk_active_entered` 出现;doctor 仅 pool-ghost 8 条合法 WARN,exit=0;Total 8252 不变 |

要点:

- **pg_dump 尾标差异**:备份末行是 `\unrestrict ...` 而非手册写的
  `dump complete` 收尾 —— 执行者未硬套手册,核实为 pg_dump 17+ 安全补丁
  (CVE-2025-8714)的 `\restrict`/`\unrestrict` 标记,`dump complete` 标记
  grep=1,非截断。判读采纳;
- **4b 的 NOTICE**(`constraint ... does not exist, skipping`)是
  DROP CONSTRAINT IF EXISTS 首跑的正常跳过,幂等设计,非错误;
- 执行者主动提出空档确认(`ops status --status checking` 为空再动手),
  避免 ALTER 的表级排他锁与消费中的写事务排队 —— 判读采纳进流程。

生产 PG 自此:factor_snapshot 无僵尸列(2026-07-06 挂账清偿);
factor_state 有状态↔时间戳一致性约束。README 迁移台账两行 ⬜ → ✅ 同批更新。
