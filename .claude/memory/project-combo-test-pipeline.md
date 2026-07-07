---
name: project-combo-test-pipeline
description: "combo 测试接口规范的最终设计与决策 (QR 提交 combo, ops 用最新数据代测)"
metadata: 
  node_type: memory
  type: project
  originSessionId: d2155cb2-4557-40fe-add2-902f6b12b34b
---

QR 团队提交 combo 给 ops 用最新数据 (含样本外 cc_2025+, QR 无权访问) 代测。接口规范 2026-06 定型并实跑验证, 文档在 `docs/combo-*.md`。

**核心模型**: combo = "算法是模型"的 Alpha。链路两段: `predict (模型算信号 → combo.npy) → backtest (gsim 跑 .npy → pnl)`。predict 由 ops 用最新数据重跑 (QR 无样本外数据); backtest 用 QR 的 config。

**三种形态**: 模型型 (有 predict/models, ✅) / 纯线性 (config 内 AlphaComboEqual 组合现成因子, 无 predict, ✅) / 只交 .npy (无推理代码, ❌ 禁收 — 拿不到样本外)。predict 是**可选段**。

**关键决策**:
- 提交物最小集 = `predict/` + `models/` + `config.<stats>.xml` (纯线性只需 config)。**无 combo.meta.json** (身份靠目录名, 数据靠 config+feature_names.csv)。
- 命名 `Combo{UnixId}{ComboName}` 无分隔 (对齐 alpha)。
- 主产物名**固定 `combo.npy`** (不声明/不协商)。
- **占位符注入**: QR 交完整 config, 环境字段写 `${RUN_DIR}` (predict 产物目录前缀) / `${DATA_ROOT}` / `${START}` / `${END}` / `${PNL_DIR}`, ops 注入真实值。前缀占位 + 相对路径消除硬编码、支持多路信号。策略段 (优化器/后处理/权重) QR 自管, ops 不碰; 环境段 ops 注入 (数据隔离)。
- predict/backtest **逻辑分离**: 单日推理分钟级 → 回测必须先批量 predict 成 .npy 再喂 gsim (不可 gsim 内逐日)。一次 predict 的 .npy 被多个 stats 复用。
- 嵌套目录: `runs/predict_<start>-<end>/<stats>/` (纯线性为 `runs/backtest_<start>-<end>/<stats>/`)。
- 四份 config: simple/bench/layer 同 StatsSimpleV5 仅 `<Stats>` 行不同 (mode 0/1/2 + index_ret + thres); opt = StatsOptV5 + RiskOpt 独立结构。改优化器参数 = 另一个 combo。
- **warmup 坑**: combo.npy 有效数据须早于回测起点 ≥1 交易日 (ProdNpyLoad 取 `combo[di-1]`), 否则首日空 — simple 静默空仓, 优化器崩 `cvxpy (0,)`。放宽优化器约束无效, 正解是 predict 留 warmup。

**已知限制 (不支持)**: 跨 combo 依赖 — opt 形态用 AlphaLoad 读另一 combo dump 的 position (如 `wbai/prod_combo_..._cut2601` 依赖 `mhe/combo_..._cut2601`)。涉及拓扑序调度, 暂不做。

**待实现 (都在 spec 未来扩展)**: `ops combo` 子命令 (读 config→替占位符→算 H→跑 gsim, 目前手动 sed 模拟) / 多 combo 批量评估排名 / 样本内优化器 API (QR 自助查样本内优化后表现, 绝不返回样本外) / 依赖链。

**`ops combo` 实现交接** (下次新 session 直接读): `tmp/combo-ops-command-design.md` — 含 ops 架构调研结果 (加子命令标准动作 / Runner / Config / sudo / state)、combo 机制全细节 (占位符注入/目录布局/真实命令/pnl文件名按Alpha-id坑/warmup)、**6 个待拍板决策** (命令粒度拆不拆阶段 / 要不要进 state / combo 根目录放哪 / 跑 qr 脚本用哪个 python / Runner 扩展 / 算力)、实现骨架、验证方式。从第 4 节决策开始即可进实现。

**滚动训练** (★ 关键: ops 训, 非 qr 训): qr 无样本外 (OS) 数据, 训不出 OS 区间后续模型, 故 **qr 交训练代码 train.py, ops 用 OS 数据滚动训出 checkpoint**。链路变三段: `train (ops 跑 qr train.py, 滚动产 checkpoint) → predict → backtest`。train.py 接口与 predict 同构 (`--data-root` / `--train-end` 训练数据截止日 / `--out-dir`), ops 滚动调用 (train-end 取 20241231/20250331/20250630...)。推理侧零改动: 已有的 `active_checkpoint_for_date` 逐日选 "≤当天最新" checkpoint, 自动季度滚动 (例: 推 2025Q1 用 ≤20241231 模型, Q2 用 ≤20250331)。checkpoint 命名须 `<prefix>_<yyyymmdd>` (文件名第二段日期数字)。范围控制用 `--train-end` 日期参数**不切快照**。训练策略 (回看窗/超参) qr 内部定; 训练侧数据隔离现以信任为主; 训练算力 (可能需 GPU, ops 机器 160/147 无 GPU) 待评估。
**已纸面验证** (玩具模型, `tmp/ComboToyRolling/`): ops 滚动调 train.py 训 4 季度 checkpoint → predict 按日期季度切 → backtest, 三段端到端通, 纯 CPU。**未验**: lhw 真实训练代码 (在 DeepStock-E2E, 未交) 能否套入 / 真实算力。规范产出 `docs/combo-train-spec.md` (给 qr 的训练代码提交要求), 待拿去要真实 train.py。

**评估制度** (QR 草拟, 待落地, 见 `tmp/combo_doc.md`): 3 个月滚动隐藏样本外 (Kaggle 式), 每轮样本外并入下轮训练 (expanding window)。

**样例** (在 tmp, 不进 git): `CombolhwEqualRawV23/` (模型型, lgbm+mlp blend), `ComboTestLinear/` (纯线性)。验证日志 `tmp/combo-test-log.md` (9 步)。相关 [[reference-gsim-xml-config]] [[project-incident-gsim-code-drift]]。
