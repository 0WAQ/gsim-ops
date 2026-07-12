# schema v2b 生产迁移执行结果(2026-07-12,执行者于 160;判读方收录)

阶段 1-5 全绿,锚点精确命中。手册 `VERIFY-SCHEMA-V2B.md`。

| 阶段 | 结果 |
|---|---|
| 1 门禁 | 173 passed, 0 skipped |
| 2 备份+基线 | 4.2M 三表全覆盖;checks=6988 / infos=8419 / entered=7530;脏元素 0 |
| 3 迁移 | COMMIT 收尾,`check 事件展开: 6988 条` == 基线,零 ERROR |
| 4 DB 形状 | state 6 业务列(删 4 列)/ history 新表+索引+双约束 / snapshot fields·tables=text[];op 计数 check=6988 / submit=8419 / entered=7530,总 22937 |
| 5 功能复验 | list Total 8252 / TEXT[] glob 58 条 / AlphaWbaiReversal 12 事件时间线(submit by migration + 11 check)/ doctor exit=0 / e2e 6 passed(108s) |

要点:

- **factor_history 预先存在(空表)**:迁移输出现 `already exists, skipping`,
  执行者停下核实 —— 按 op 计数证明表内原本零行,合成事件无污染。根因:
  放行文案让执行者在开窗前用**新代码**跑 `ops status --status checking`
  做空档确认,repository 首次触达 PG 时 `ensure_schemas` 引导建了空表
  (设计内幂等行为)。教训:窗口前的"只读确认"若用新代码跑,也算新代码
  的首次 PG 触达 —— 无害(建表幂等 + 迁移 count-verify 兜底),但下次
  手册把空档确认放进窗口语义里说明;
- **doctor exit=1 虚惊**:`| head` 管道 SIGPIPE 污染 PIPESTATUS,重跑无管道
  REAL exit=0 —— 执行者未放过异常退出码,处置正确;
- 窗口纪律:迁移先行、160 分支代码复验、150/144/170 保持旧代码禁跑,
  待 v2b PR 合并后阶段 6 滚存收窗。

生产 PG 自此:factor_history 22937 条事件(6988 真实 check + 15949 条
actor='migration' 合成);factor_state 纯状态机 6 列;fields/tables TEXT[]。

## 阶段 6 · 四机滚存收窗(2026-07-12,PR #15 合并后)

四机 HEAD 全部 34e25d2(main),`ops list` 冒烟 Total 8252 一致,窗口解除。
过程中两个部署坑(执行者现场排障):

- **uv tool 部署形态**:160/170(以及排障后确认的 150/144)`ops` 都是
  `uv tool install` 的全局命令 —— **git pull 不等于部署**,须
  `uv tool install --reinstall .` 才跑新代码(170 首次冒烟报 UndefinedColumn
  rejected_at 即旧 commit 521533f 查已删列);
- **root 属主 pycache 卡重装**:150/144 的 uv tool 目录里有 root 属主
  `__pycache__`(2026-07-08 某次 sudo 跑 ops 以 root 写的 .pyc),
  `--reinstall` 递归删不掉。绕法:整棵 `tools/ops` rename 成
  `ops.broken.<ts>`(只需父目录写权限)+ `uv tool install --force .`。
  **遗留**:150/144 各一个 `~/.local/share/uv/tools/ops.broken.<ts>`
  孤儿目录待用户 sudo rm -rf。

schema v2(v2a + v2b)至此全部落地。
