# Doctor

`ops doctor` —— 盘 ↔ PG 数据对账(2026-07-12 立项;设计经三视角面板 + 双评审)。
setup 管部署形态,doctor 管数据一致性;前身 `ops health` 因"--fix 写没人读的
僵尸表"退役(Wave 2),其对账职能在此落地。

## 形态

- `ops doctor`(缺省)= **纯只读报告**:零 sudo(注册处不 mark_write)、零写
  (连报告文件都不落 —— 要留档 `--format json > file`);
- `ops doctor --fix <族>` = 按族点名修复(可重复;`_FixAction` 置
  is_write_command,setup `_CheckAction` 的反相):先全量扫描,**逐族独立
  确认**(一个 y 只授权一个族;FixPlan 三句话逐字打印 —— 打印的就是执行的),
  非 TTY 无 `-y` 拒绝(exit 2);
- 退出码:0=无 FAIL 级漂移 / 1=有(--fix 后按 residual 余量,锁跳过仍算余量)
  / 2=用法错误、PG 不可达(infra/pg.probe 有界直连秒级硬失败)。

## v1 七族(注册表 `checks.py::FAMILIES`,新增族 = 加一行;severity 在 kind 级)

| 族 | scope | fixable kind | report-only kind(转介) |
|---|---|---|---|
| pool-ghost | global | ghost → unlink 池文件 | wrong-pool / missing(approve 豁免合法)/ ghost-info-orphan(先诊断)/ alien |
| snapshot-stale | pg | illegal(entered_at 空带快照)→ repo.discard_snapshot | mismatch → `scripts/postgres/migrate_snapshot_at.py` 一次性迁移(不给 doctor 开 UPDATE) |
| info-orphan | pg | — | orphan(FAIL;无产物→可贴 `ops rm`,有产物→人工) |
| src-drift | global | — | lib-missing(FAIL,源码丢失)/ src-orphan(**alpha_src 永不进删除集**) |
| staging-drift | global | — | orphan-dir→`ops clear` / missing-dir→`ops cancel --force`(不复制第二套删除逻辑) |
| artifact-orphan | global | pack-tmp(点开头 .npy.tmp,mtime>24h)→ unlink | pnl-orphan / feature-orphan(**v1.1 基线判读后再议放闸**)/ alien |
| dump-orphan | **host** | orphan → rmtree 本机 sidecar 目录 | —(每机 sidecar 独立,各机各跑) |

**显式拒收(防回潮,见 checks.py 模块 docstring)**:"ACTIVE 缺 dump"检查
(dump 产在消费机,本机不可判 —— health 结构性误报根源)、dump 日期缺口
(25s 深扫无消费方)、一切"补数据"类 fix、PG host 登记夹带。

## 删除闸(`guards.py`,所有 fixer 唯一执行出口 —— 五道闸)

逐条非阻塞 factor_lock → 锁内 repo 新读重验 verdict(TOCTOU 双钥)→ ACTIVE
绝缘集中断言(点开头 .tmp 形状豁免)→ 路径闸(fixer.resolve 现场重拼 +
realpath 落在 allowed_roots 白名单内 + alpha_src/alpha_pnl/staging 禁区双保险)
→ 形态闸(unlink 只删文件 / rmtree 只删真实目录不跟软链)。ENOENT / 重验不
成立 → VANISHED(并发 rm 抢删属正常);逐条独立,中断重跑即重扫收敛。
判定函数写错最多"该删没删",不可能升级为误删。

## 结构

- `findings.py` —— Finding(kind 级 severity)/ FamilyResult(population 分母
  + fix_log 记账 + residual)/ Inventory(采集产物)/ FixPlan(action/target/
  keeps 三句话必填);
- `checks.py` —— 族注册表 SSOT + 判定纯函数 `(Inventory) -> list[Finding]`
  (单测零 I/O 表驱动);`classify_pool` 移植自 reconcile_bcorr_pools.py
  (2026-07-11 生产 622 鬼影清零实证,判定表不改语义;脚本本体留在
  claude/ops-rotate-and-reconcile 分支作历史,不进 main);
- `engine.py` —— probe → 一次 `repo.find(include_submitted=True)` + 各区浅
  iterdir(刻意不用 LibraryScanner 25s 深扫)→ scan → (confirm 后)fix。
  单族崩溃 / 区 PermissionError 记 skip 不拖垮整份报告。零展示;
- 渲染在 `ops/cli/doctor.py`(C9);`--format json` 时明细全量到 stdout,
  人读输出转 stderr。

测试:`tests/test_doctor.py`(判定纯函数,无 PG)+ `tests/test_doctor_fix.py`
(PG 组:邻居不碰断言 / 幂等双跑 / TOCTOU / 锁竞争 / ENOENT / info 孤儿剔除)。

## v2+ 挂账

pnl/feature 孤儿放闸(待 160 首轮基线判读)、dump 日期缺口(先有具名消费方)、
clear/cancel 收编、PG 记产物所在 host(跨机对账元问题)。
