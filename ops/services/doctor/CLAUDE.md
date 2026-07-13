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

## v1 八族(注册表 `checks.py::FAMILIES`,新增族 = 加一行;severity 在 kind 级)

| 族 | scope | fixable kind | report-only kind(转介) |
|---|---|---|---|
| pool-ghost | global | ghost → unlink 池文件 | wrong-pool / missing(approve 豁免合法)/ ghost-info-orphan(先诊断)/ alien |
| snapshot-stale | pg | —(v3 起全族只报告;原 illegal kind + discard fixer 随测得快照语义作废退役) | mismatch(snapshot_at ≠ 最近 check 事件 at,无事件锚 entered_at)/ unanchored → 时间戳修正走一次性脚本(不给 doctor 开 UPDATE) |
| timeline-drift | pg | — | created-after-submitted(WARN;词汇表不变量 created_at <= submitted_at,submitted_at=NULL 设计内值跳过;修正走一次性脚本,2026-07-13 legacy 清理批) |
| info-orphan | pg | — | orphan(FAIL;无产物→可贴 `ops rm`,有产物→人工) |
| src-drift | global | — | lib-missing(FAIL,源码丢失)/ src-orphan(**alpha_src 永不进删除集**) |
| staging-drift | global | — | orphan-dir→`ops clear` / missing-dir→`ops cancel --force`(不复制第二套删除逻辑) |
| artifact-orphan | global | pack-tmp(点开头 .npy.tmp,mtime>24h)→ unlink;**feature-orphan(PG 全无记录的 <name>.vN.npy)→ unlink(v1.1 放闸,2026-07-12 首轮基线判读后)** | pnl-orphan(无判读材料,待 v1.2)/ alien |
| dump-orphan | **host** | orphan → rmtree 本机 sidecar 目录 | —(每机 sidecar 独立,各机各跑) |

**显式拒收(防回潮,见 checks.py 模块 docstring)**:"ACTIVE 缺 dump"检查
(dump 产在消费机,本机不可判 —— health 结构性误报根源)、dump 日期缺口
(25s 深扫无消费方)、一切"补数据"类 fix、PG host 登记夹带。

## 删除闸(`guards.py`,所有 fixer 唯一执行出口 —— 五道闸)

逐条非阻塞 factor_lock → 锁内 repo 新读重验 verdict(TOCTOU 双钥)→ ACTIVE
绝缘集中断言(点开头 .tmp 形状豁免)→ 路径闸(fixer.resolve 现场重拼 +
realpath 落在 allowed_roots 白名单内 + **双层禁区**:包含型 alpha_src/
alpha_pnl/staging 整树不可入;等值型 目标绝不许就是/包含任何 config 声明的
数据根 —— 专拦 allowed_roots 与扫描源同一 config 键派生时的错配自引用,
2026-07-12 对抗评审 major)→ 形态闸(unlink 只删文件 / rmtree 只删真实目录
不跟软链)。ENOENT / 重验不成立 → VANISHED(并发 rm 抢删属正常);逐条独立,
中断重跑即重扫收敛。判定函数写错最多"该删没删",不可能升级为误删。

**dump-orphan 错配绊线**(同批评审修复):alpha_dump 指错一级(config 少写 /
sidecar 软链错指)时扫到的是 alphalib 根 —— scan 检测区内条目撞库区名
(alpha_src/staging/…)即抛 `FamilySkip` 整族弃权零发现,指引先跑
`ops setup --check`。等值闸兜绊线拦不住的软链错指形态。

**锁协议前提**:五道闸的 TOCTOU 防线假设**所有状态写入方持 factor_lock**。
2026-07-12 对抗评审发现 backfill 是全库唯一无锁 register 方(会在"重验通过 →
删除"窗口把因子登记成 ACTIVE),同批补齐(逐因子包锁);2026-07-13 backfill
命令整体退役,豁免面清零 —— 新增状态写入路径必须持锁,否则击穿本闸。

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

pnl 孤儿放闸(基线 pnl-orphan=0,无判读材料)、dump 日期缺口(先有具名消费方)、
clear/cancel 收编、PG 记产物所在 host(跨机对账元问题)、长/短名归一配对
(L2,ybai 双命名致配对假阴性 —— 一次性历史,先档案不写代码)。

**v1.1(2026-07-12,DOCTOR-V11-TRIAGE 判读后)**:feature-orphan 放闸(经用户
拍板);107 个 src-orphan 属历史清理残渣,由 `scripts/cleanup_src_orphans.py`
一次性名单化处置(**alpha_src 仍不进 doctor 删除集** —— 铁律不破,脚本是判读
后的一次性通道);L1 写入侧修复:XML author 小写归一(factormeta)+ birthday
区间校验(submit_one)。
