# Produce

在库(ACTIVE)因子的 alpha_dump 日增生产:`ops produce` 把每个因子的 dump 从
`production_start`(config,check 历史窗口 20251231 之后)补到最新就绪交易日。
**无状态命令**:不写 factor_history(每日全库 = 海量噪音事件)、不 transition ——
dump 文件本身即记录,幂等重跑收敛。

## 与 check 的边界

check 是验证(cc_2025 冻结窗口,可复现,`services/check/xml_prepare.py` 三个窗口
常量一字不动);produce 是生产(cc_all 持续日增)。两个数据根是**两个事实族**
(`path.nio_data_path` vs `produce.nio_data_path`),不是重复配置。
dump 是本机 sidecar:在哪台跑就落哪台,生产消费机 = 170(与 check 消费同机,
dump 才连续)。

## 就绪三重规则(`dates.py`)

`ready(D) ⇔ D ∈ 数据根交易日轴 ∧ D ≤ canary .meta.lastDate(gsim 严格按 .meta
截断,轴物理长度可超出可见范围)∧ canary close.npy 第 idx(D) 行有非 NaN
(build_cc 末行可能 NaN 占位)`。latest_ready 自闸门日向前回退 ≤ READY_BACKOFF。
缺省目标日自动落 latest_ready;显式 `--date` 不就绪响亮拒绝,不静默换日。

## 工作区与 XML(`xml_prepare.py`)

永不碰 alpha_src 原件(归档 XML 被 check 拆雷:输出指 /tmp/alphalib、窗口残留
long_backtest、Data 项锁 cc_2025):copytree 副本 → `rewrite_module_path` →
改副本(窗口 = 缺失段;Constants + Data 项数据根整体换到 produce 根;dump 开、
pnl 关;checkpointDir 指工作区且**每次全新** —— 陈旧 checkpoint 会被 gsim load
崩)。普通因子单日/短段回测无 warmup 问题:gsim 在 generate(di) 内部读 cc 历史
(combo 的 --predict-start 是预 predict npy 的起点边界,与此无关)。

## 缺失推导与安装语义

- 缺失 = 轴上 [production_start, target] − 本机已有(**require_both**:v1∧v2 齐
  才算有,安装中断的半日按缺失计,重产覆盖即自愈);一次 gsim 覆盖
  [min(missing), max(missing)],段内已存在的完整日重算了也**丢弃不装**。
- 覆盖策略由 wanted 集承载:缺省 wanted = 缺失集(绝不触碰已有);`--force`
  wanted = 显式窗口(须 `--date` 锚定作用域 + apt 确认,`--start` 不得早于
  production_start —— 2025 及以前是 check 的产物,produce 永不触碰)。
- 安装:工作区 → sidecar 跨文件系统,tmp + `os.replace` 原子;全 NaN 日照常
  安装但 warn 计数(无效日合法,compliance 同语义;拒装会每日重跑死循环)。
- **restage 语义**:重过 check 的归档会把 dump 整目录换成 ≤20251231 产物,
  2026+ 段被抹是既有行为 —— 下次 produce 视为缺失自动重填,无需特判。

## 并发 / 退出码

ProcessPool + worker 内 factor_lock(跨机 PG advisory)+ 锁内复验 ACTIVE
(TOCTOU;repo 在 worker 内现构造,见 check.py::_repo)。workers=1 走进程内
串行(测试注入 fake backtest 的路径)。失败 > 0 退出码 1(未来 cron 的判据,
doctor FAIL→1 同例)。

## 后议(见 .claude/plans.md)

feature 侧(pack --date 接线 / PACK_L 扩行)、cron 节奏化、combo 日增、
滞后表因子 per-factor 就绪。
