# Combo Calling Convention v0.2

> v0.2 (2026-06): 接口经 `tmp/combo_test/` (后改名 `tmp/CombolhwEqualRawV23/`) 逐步最小化重建 + 实跑验证后定型。
> 取代 v0.1 的 MLflow Projects 路线 (MLproject/python_env.yaml/分目录) —— 那套实践中未走通
> (`mlflow run --env-manager local` 因 torch 不在 ops venv 失败, 实际一直直接用 gsim venv 跑)。
> 验证过程见 `tmp/combo-test-log.md`, 架构推导见 `docs/combo-architecture-decision.md`。

## 背景

QR 团队没有最新数据 (cc_2025+) 访问权限。combo 的 predict 必须由 ops 用最新数据重跑,
再走 gsim backtest。本文档定义 combo 提交的最小接口契约。

**核心认知** (详见 ADR): combo = 一个"算法是模型"的 Alpha —— `predict` 阶段用模型在 feature 上
算出信号 .npy, 再喂 gsim 回测。基础链路两段:

```
predict (combo 模型算信号 → .npy)  →  backtest (gsim 跑 .npy → pnl)
```

**滚动训练时三段** (见下"滚动训练"章节): qr 无样本外数据训不出后续模型, 故交训练代码, 由 ops
用样本外数据滚动训出 checkpoint:

```
train (ops 用 OS 数据跑 qr train.py → 按日期 checkpoint)  →  predict  →  backtest
```

## combo 形态: predict 是可选段 (★)

combo 信号可以是模型算的, 也可以是现成因子的线性组合。据此分三种形态:

| 形态 | 信号怎么来 | predict 段 | predict/models | 处理 |
|---|---|---|---|---|
| **A 模型型** | predict 跑模型算 .npy | **有** | 有 | ✅ 完整支持 (v0.1 验证) |
| **B 只交 .npy** | qr 自己算好的成品 .npy | 无 | 无 | ❌ **禁收** |
| **C 纯 gsim 线性** | config 里 `<Alphas combo>` 线性组合 cc 现成因子, 无模型 | **无** | 无 | ✅ 支持 (predict 设为可选段, 已验证) |

- **A**: ops 用最新数据重跑 predict → `${RUN_DIR}/combo.npy` → backtest。产物 `runs/predict_<start>-<end>/<stats>/`
- **C**: 无 predict, 信号是 cc 现成因子 (`${DATA_ROOT}/...`) 的线性组合, 直接 backtest。
  产物 `runs/backtest_<start>-<end>/<stats>/` (`backtest_` 前缀, 区别于 A 的 `predict_`); config 里**无 `${RUN_DIR}`**
- **B 禁收的理由**: qr 无样本外 (cc_2025+) 数据权限, 交来的 .npy 只到样本内截止日, ops 拿不到样本外段,
  无法做 out-of-sample 评估 (违背整个评估目的)。必须交 predict 代码让 ops 用最新数据重跑。
  → 区分 B 和 C: B 是"模型算的成品 .npy 缺 predict 代码"(禁); C 是"无模型, 信号即现成因子线性组合"(可)。

## 两个"根"的区分 (★ 概念基础)

| 概念 | 是什么 | 路径 | 谁的 | 数量 |
|---|---|---|---|---|
| **ComboProject 根** | 一个 combo 提交物的根目录 | `Combo{UnixId}{ComboName}/` | qr 交付的**输入** | 一个 combo 一个 |
| **`${RUN_DIR}`** (Combo run 根) | 某一次评估跑出的产物目录 | `<ComboProject>/runs/predict_<start>-<end>/` | ops 跑出的**输出** | 滚动评估每轮一个 |

- ComboProject 根 = 输入 (predict/models/config), 相对固定, ops 本来就知道在哪 → **不需要占位符**
- `${RUN_DIR}` = 输出 (这一轮的 .npy), 每轮新增 → config 里用它作 npy 前缀
- 二者必须分开: 否则"输入"和"输出"混在一起 (就是早期 output/ 杂乱的根因), 且 `${RUN_DIR}` 字面别和 project 根混

## combo 提交物 (最小集)

**命名**: combo 目录名与 alpha 对齐 —— `Combo{UnixId}{ComboName}` (如 `CombolhwEqualV23`), 类比 `Alpha{UnixId}{FactorName}`。

经实跑验证, 一个 combo 提交需要:

```
Combo{UnixId}{ComboName}/
├── predict/               # 推理代码 (算信号, 入口 predict.py)       [A 模型型必交; C 纯线性无]
├── models/                # 模型权重 (按日期命名的 checkpoint + feature_names.csv)  [A 必交; C 无]
├── config.simple.xml      # backtest config, 每个 stats 一份 (环境字段写占位符)  [必交]
├── config.bench.xml       #   simple/bench/layer 仅 <Stats> 行不同 (mode/index_ret/thres)
├── config.layer.xml       #
├── config.opt.xml         #   opt 是独立结构 (StatsOptV5 + RiskOpt 多腿), 非套壳
└── runs/                  # ★ 所有产物统一出口 (提交时为空, ops/qr 跑测时生成)
    └── predict_<start>-<end>/ #   A: 一次 predict (按区间命名); C 无此层, 用 backtest_<start>-<end>/
        ├── combo.npy         #     predict 主信号 (固定名 combo.npy); 多腿/中间产物由 predict 自定
        ├── combo.npy.meta.json   # predict 自动落的产物元信息 (数据版本/区间; 非提交物)
        ├── simple/           #   config.simple.xml 注入后跑的 backtest
        │   ├── pnl/
        │   ├── summary.txt
        │   └── config.injected.xml   # ops 注入后的 config (审计)
        ├── bench/  layer/    #   各自对应 config.<stats>.xml, 复用同一份 .npy
        └── opt/              #   config.opt.xml; 多 checkpoint/ positions/ (优化器运行时产物)
```

> 身份 (作者/combo 名) 由目录名 `Combo{UnixId}{ComboName}` 表达; 数据依赖由 config + `feature_names.csv`
> 自描述。**无提交物元信息文件** (不需要 combo.meta.json)。
> 形态 C (纯 gsim 线性) 提交物 = `config.<stats>.xml` (无 predict/models);
> 产物落 `runs/backtest_<start>-<end>/<stats>/` (无 predict 层)。详见上面"combo 形态"章节。

**predict / backtest 逻辑分离** (★ 核心):
- **predict 贵且少变** (数据/模型/区间变才重跑, 全段几十分钟), **backtest 便宜且常变** (换 stats 视角, 秒级)
- 一次 predict 的 .npy **被多个 backtest 复用** —— 换 stats 不重跑 predict
- 嵌套体现归属: backtest 在它依赖的 predict 目录下, 生命周期一致 (删一次 predict 连带其所有 backtest)

**目录命名**:
- predict 层 = `predict_<start>-<end>` (A 模型型; `predict_` 前缀标明目录类型, 区间对滚动评估友好; 数据根/版本看 `combo.npy.meta.json`)
  - C 纯线性无 predict 产物, 此层为 `backtest_<start>-<end>` (`backtest_` 前缀)
- backtest 层 = **裸 stats 名** (`simple` / `bench` / `layer` / `opt` ...) —— 与根目录 `config.<stats>.xml` 一一对应
  - 注: **改优化器参数 = 另一个 combo** (qr 语义), 不在同一 runs 下并列; 故 backtest 层不编码参数变体


**占位符指向**:
- `${RUN_DIR}` = `runs/predict_<start>-<end>/` (predict 产物目录, npy 前缀)
- `${PNL_DIR}` = `runs/predict_<start>-<end>/<stats>/pnl/`

## backtest 层: 多个 stats (每个 stats 一份独立 config)

一次 predict 的 .npy 下可挂多个 backtest, 每个 stats 一份根目录 `config.<stats>.xml`,
注入后跑, 落到对应的 `runs/predict_<start>-<end>/<stats>/` 子目录。**已实跑验证 (4 份共存)**。

**普通 stats: simple / bench / layer** — 同一 `StatsSimpleV5`, 只差 `<Stats>` 那一行:
- `config.simple.xml`: mode 0
- `config.bench.xml`: mode 1 + `index_ret` (对标指数收益)
- `config.layer.xml`: mode 2 + `index_ret` + `thres` (分层阈值)
- 三份**共享同一 combo 信号 + 同一套后处理**, 只有 `<Stats>` 行不同 (其余内容重复, 换取"所见即所跑", 无套壳逻辑)

**特殊 stats: opt** — `config.opt.xml`, `StatsOptV5` + RiskOpt 优化器, **独立结构** (非套壳):
- `<Alphas combo=...>` 多腿容器 + Power→Hump→RiskOpt 后处理 + 额外数据依赖
  (HS300/ZZ500/asharebalancesheet/指数权重) + 优化器参数 (maxPct/gamma/deltaind...)
- 多 `${CHECKPOINT_DIR}` 占位符 (优化器要); 运行时多落 `checkpoint/` `positions/` 在自己 `opt/` 下
- **opt 的优化器参数算 ops 统一口径还是 qr 策略 → 待定** (决定 opt config 谁维护; 当前是 qr 提交)

> 命名约定: 根目录 `config.<stats>.xml` ↔ 产物 `runs/predict_<区间>/<stats>/` 一一对应。
> ops 注入逻辑: 对每个 `config.<stats>.xml` 替占位符 → 跑 gsim → 落对应 `<stats>/`。

**不需要交** (实测多余):
- `backtest/*.py` —— 回测零 .py 依赖, gsim 自带 `ProdNpyLoad` 读 .npy
- `run_all.sh` / `MLproject` / `python_env.yaml` —— ops 直接调 predict, 不走 mlflow
- `configs/` (qr 私有超参) —— 接口不关心, 在 predict 内部
- 预跑的 `.npy` / 数据 —— ops 用最新 cc 重跑 (提交时 `runs/` 应为空)

> 注: 滚动训练场景下, **训练代码 (train.py) 是必交的** (ops 用 OS 数据滚动训), 见下"滚动训练"章节。
> 非滚动 (qr 已交全部 checkpoint) 则不需要 train.py。

## 接口① — predict 调用约定

ops 调用 combo 的 predict, 算出信号 .npy。combo 内部怎么读 feature / 选模型 / lookback **qr 自由, ops 不管**。

**ops 给 (输入)**:

| 参数 | 含义 | 备注 |
|---|---|---|
| `data_root` | cc 数据根 | ops 注入 (回测=cc_2025, 日增=当日最新)。combo **不许** hardcode |
| `start` / `end` | 推理区间 yyyymmdd | combo 必须接受**任意**区间, 不许假设固定截止日 |
| `device` | cpu/cuda/auto | ops 无 GPU, 默认 cpu; 建议支持 auto 探测 |
| `output-dir` | 输出目录 | combo 在此目录下产出 .npy (可多个, 见多腿)。ops 实际传 `runs/predict_<start>-<end>/`; qr 本地自测随便指一个目录 |

**combo 产出 (输出)**:
- **主产物**: 一个 float64 memmap `.npy`, **文件名固定为 `combo.npy`**
  - shape = `(data_root 日期数, 股票数)` —— **shape 从 data_root 推导, 不许写死**
  - `[start, end]` 外的行可 NaN
- **中间产物** (可选): predict 内部可落多个 npy (如 lhw 的 lgbm 腿 / mlp 腿), ops 不管, backtest 不用

**★ 主产物名固定** (闭合 predict 输出与 config 引用的缺口):
- predict 默认 `--out-name` = `combo.npy` (ops 不传也对)
- config 里 `npydata="${RUN_DIR}/combo.npy"` 引用同名
- → 硬性规定固定名, 无需声明/协商; ops 注入时直接对齐

**回测与日增是同一个接口, 只换 start/end**:
- 回测 (评估): `start~end` 全段 → 全段 .npy → gsim backtest → pnl
- 日增 (实盘): `today~today` 单天 → 当天信号 → 出持仓

→ 日增是 "start=end=今天" 的特例, combo predict 不 hardcode 日期就天然同时支持, 无需第二套代码。
  (qr 已确认: 单日推理分钟级, 无状态前馈, lookback 从 cc 历史现拿现算 → 此模型成立。)

> 不引入"逐日 generate_day(di)"抽象: 单日分钟级, 回测全段若 gsim 内逐日实时 = 数天不可行,
> 所以回测**必须**先批量 predict 成 .npy 再喂 gsim。predict 内部 qr 想怎么向量化都行。

## 接口② — backtest config (占位符注入)

**问题**: backtest config 大多是 qr 自己的 (后处理算子链 / 优化器及参数 / 对标指数 / booksize /
组合权重 = qr 策略表达), ops 不能用通用模板写死。但 ops 必须改写环境字段 (数据路径/日期/npy 指向)
指向评估数据, 并防数据隔离泄露。

**方案 (qr 零改造负担)**: qr 交他**自己的完整 config (每 stats 一份 config.<stats>.xml)**, 只把"该 ops 填的地方"写成固定占位符,
ops 注入真实值后跑。策略段 qr 完全自由, ops 不碰; 环境段 ops 注入, qr 碰不到。

### 占位符集

| 占位符 | ops 注入 | 性质 |
|---|---|---|
| `${RUN_DIR}` | predict 产物目录 (`runs/predict_<start>-<end>/`) | **目录前缀**, 撑任意多腿; **仅 A 模型型有** (C 纯线性无 predict 产物, config 里不出现) |
| `${DATA_ROOT}` | 评估数据根 (cc_2025) | **目录前缀** (数据隔离核心) |
| `${START}` `${END}` | 评估区间 | 值 |
| `${PNL_DIR}` | backtest 输出 (`runs/<predict\|backtest>_<start>-<end>/<stats>/pnl/`) | 目录 |

**ops 自动处理 / 固定, qr 不写**:
- `H` (日期数): ops 注入时读 data_root 日期数自己算 (ProdNpyLoad 的 H 有默认 3900, 但 ops 按实际算, 防 cc 变长用错)
- `checkpointDir`: ops 内部固定默认
- rawdata 路径 (HS300/ZZ500/asharebalancesheet 等, `/datasvc/rawdata`): 公共固定, 不占位

### ★ 关键: 前缀占位 + 相对路径 (消除 config 路径硬编码)

config 最大痛点是 npy 路径硬编码, 多腿时占位符还会数量爆炸。解法: **只占共同前缀 (目录), 相对路径留给 qr 自治**。

qr config 里这样写 (任意多腿, 永远只用一个 `${RUN_DIR}`):

```xml
<!-- 主信号 / 多个模型腿: 用 ${RUN_DIR} 前缀 + qr 自己的相对路径 -->
<Alpha id="LGBM_leg" module="ProdNpyLoad" npydata="${RUN_DIR}/combo_lgbm_v23.npy" weight="0.5" .../>
<Alpha id="MLP_leg"  module="ProdNpyLoad" npydata="${RUN_DIR}/mlp/signal.npy"    weight="0.5" .../>

<!-- 外部因子 (cc 现成的, 如 pwang 行业): 用 ${DATA_ROOT} 前缀 + 相对路径 -->
<Alpha id="Pwang"    module="ProdNpyLoad" npydata="${DATA_ROOT}/DmgrPwang_industry_.../...combo.npy" weight="0.1" .../>
```

- ops 只注入 `${RUN_DIR}` / `${DATA_ROOT}` 两个前缀; 子目录/文件名由 qr 定, ops 不碰
- predict 输出几个 npy、怎么组织目录, 由 qr 自治 (config 相对路径与 predict 输出结构一致即可)
- **qr config 里不出现任何绝对文件路径, 全是 `${前缀}/相对路径`**

### 注入示例

qr 模板片段:
```xml
<Constants niodatapath="${DATA_ROOT}" .../>
<Universe startdate="${START}" enddate="${END}" .../>
<Stats ... pnlDir="${PNL_DIR}"/>
<Alpha ... npydata="${RUN_DIR}/combo.npy" .../>
```
ops 注入后:
```xml
<Constants niodatapath="/datasvc/data/cc_2025" .../>
<Universe startdate="20240102" enddate="20251231" .../>
<Stats ... pnlDir=".../runs/predict_20191201-20251231/simple/pnl"/>
<Alpha ... npydata=".../runs/predict_20191201-20251231/combo.npy" .../>
```

## 滚动训练 (★ ops 用 OS 数据滚动训 checkpoint)

**动机**: qr 无样本外 (OS) 数据, 训不出样本外区间后续的模型。滚动训练 = 每段用"该段开始前"的数据
训一个模型, 再用它推下一段, 逐段往前。例 (季度滚动):

```
推 2025Q1 → 用 ≤20241231 训的模型
推 2025Q2 → 用 ≤20250331 训的模型
推 2025Q3 → 用 ≤20250630 训的模型
```

qr 训不出 20250331 之后的 (无 OS 数据), 故 **qr 交训练代码, ops 用 OS 数据滚动训出 checkpoint**。

### 接口: train.py (与 predict 同构)

qr 在 `predict/` (或 `train/`) 下提供训练入口 `train.py`, ops 滚动调用:

| 参数 | 含义 |
|---|---|
| `--data-root` | cc 数据根 (ops 注入, 含 OS) |
| `--train-end` | 训练数据截止日 —— 只用 ≤ 该日数据训 (qr 须严格遵守) |
| `--out-dir` | checkpoint 输出目录 |

ops 滚动驱动: `--train-end` 依次取 `20241231 / 20250331 / 20250630 / ...`, 每次产出
`<prefix>_<train-end>.{pkl,pt}` 落到模型目录。

### 与滚动推理衔接 (机制已就绪)

ops 训出按日期命名的 checkpoint 后, predict 的 `active_checkpoint_for_date` (已实现)
逐日选 "≤当天最新" checkpoint —— 自动实现季度滚动, 推理侧零改动:

- checkpoint 命名须为 `<prefix>_<yyyymmdd>...` (文件名第二段是日期数字, 供 ops 解析选择)
- 粒度任意 (年/季/月), 取决于 ops 训出多少个 checkpoint; qr 训练代码不限定粒度
- 防 look-ahead 天然成立 (绝不会用未来训的模型推过去)

> 现阶段研究员训练侧的数据隔离**以信任为主** (qr 训练代码自觉守 `--train-end`, 不读之后数据)。
> 算力 (训练可能需 GPU / 耗时远超 predict) 待评估, 不在本规范。

## Warmup: combo 数据起点必须早于回测/实盘起点 (★ 实测踩坑)

gsim 的 npy-load (`ProdNpyLoad`) 在 `generate(di)` 取**前一天**: `self.combo[di-1]` (delay 防 look-ahead)。
因此 **combo.npy 有效数据必须比回测/实盘 startdate 早至少 1 个交易日**, 否则起点当天取 `combo[di-1]`
落在 NaN 上 → alpha 全 0。后果按下游严格度:

| 下游 | 首日 alpha 全 0 |
|---|---|
| `StatsSimpleV5` | 容忍, 当天静默空仓 (易被忽略) |
| `StatsOptV5` + 优化器 | **直接崩** `cvxpy Invalid dimensions (0,)` |

**实测**: combo predict `--start 20200102` + 回测从 20200102 → 优化器第一天取 `combo[20191231]`=NaN → 崩。
放宽优化器约束**无效** (约束只管"给定 alpha 怎么分配", 救不了"输入全空")。

**正解**: predict 时 start 比回测 start 提前若干交易日 (覆盖 Decay warmup + 至少 1 天 di-1 lag)。
例: 回测从 20200102, predict 用 `--start 20191201`。已实测从原起点 + 原生产参数干净跑通。

> 这条 combo 特有: 普通 alpha 在 generate 里直接读 cc (有完整历史), 无 npy 起点边界问题;
> combo 信号是预先 predict 的 .npy, 有人为起点, di-1 撞 NaN 是 combo 独有的坑。

## 数据访问契约

qr 在自己机器准备 combo, 只能用截至样本内截止日的 cc。ops 跑 predict 时用本地完整 cc (含样本外),
qr 碰不到 —— 靠 `${DATA_ROOT}` 由 ops 注入 (qr config 里只有占位符, 填什么 ops 说了算)。

理由: qr 若能看到样本外, 模型选择/超参会反向过拟合, 违背 out-of-sample 评估目的。

详细评估制度 (3 个月滚动隐藏样本外 + 样本内优化器 API) 见 `tmp/combo_doc.md`, 待落地。

## 反检查清单 (ops 跑前自动校验)

| 检查项 | 不通过 |
|---|---|
| `config.<stats>.xml` (≥1 份) 齐全 (A 形态另需 `predict/` + `models/`) | 拒收 |
| (A 形态) `predict/predict.py` 接受 data_root/start/end/device/output-dir | 拒收 |
| (A 形态) predict 主产物固定为 `combo.npy`, config 引用 `${RUN_DIR}/combo.npy` | 拒收 |
| 形态判定: 有模型成品 .npy 但无 predict 代码 = B 形态 | 拒收 (见 combo 形态) |
| `config.<stats>.xml` 里**无绝对文件路径**, npy 引用全是 `${RUN_DIR}/...` 或 `${DATA_ROOT}/...` | 拒收 |
| `config.<stats>.xml` 无占位符外的 qr 个人路径 (`/home/xxx/...`) | 拒收 |
| `config.<stats>.xml` 无待填占位符 (`"modify to your XXX.so"`) | 拒收 |
| 外部 .so / 因子在 ops 这边可访问 (优化器等是公共的) | 拒收 |
| (A 形态) predict warmup 起点早于回测起点 ≥1 交易日 | 警告 |
| (A 形态) `--device cpu` 跑通 predict | 警告 |

## 已验证场景 (全部实跑)

| 场景 | 结果 |
|---|---|
| 最小集 (predict+models+config) 独立 predict→backtest | ✓ 2025 sharpe 0.61, 与完整包 bit 级一致 |
| 纯 config backtest (零 .py, gsim 自带 ProdNpyLoad) | ✓ |
| 占位符注入 (单腿) | ✓ |
| 多腿 + 优化器 (v0.2 注入版, Hump×3 + RiskOpt20) | ✓ status optimal 全段 |
| 多腿 `${RUN_DIR}` 前缀 + 相对路径 | ✓ 两腿各自加载跑通 |
| 主产物名固定 combo.npy (不传 --out-name 默认产出 combo.npy) | ✓ |
| 四份 config.<stats>.xml 共存 (simple/bench/layer/opt 各跑各落) | ✓ 四 stats 各占子目录 |
| C 形态纯线性 (无 predict/无 ${RUN_DIR}, cc 现成因子组合) | ✓ 直接 backtest 跑通 |
| **`ops combo run` 端到端** (模型型 predict→backtest / 纯线性直接 backtest / 四 stats 含 opt) | ✓ 命令产出与手动一致 (simple sharpe 4.96 一字不差) |

> 样例 combo: `tmp/CombolhwEqualRawV23/` (A 模型型), `tmp/ComboTestLinear/` (C 纯线性)。验证日志: `tmp/combo-test-log.md`。

## 已知限制 (当前不支持)

**跨 combo 依赖**: 某些 opt 形态不自包含, 而是用 `AlphaLoad` 读**另一个 combo** dump 的每日 position
(`alphaDir` 指向上游 combo 的 `combo_eq/position/`), 再过优化器。
实例: `wbai/prod_combo_mhe_tl_fguo662_wangpy_cut2601` 依赖 `mhe/combo_mhe_tl_fguo662_wangpy_cut2601`
(前者是后者的 opt 形态)。

问题: 本规范假设 combo 自包含 (predict → 自己的 .npy → backtest)。跨 combo 依赖下, 下游的样本外评估
要求上游也在样本外重跑 (产出新 position), 涉及依赖解析 + 拓扑序调度, 当前接口不覆盖。

当前处理: **暂不支持依赖链**。需要 opt 的 combo, 应把上游逻辑内联进自己的 config (自包含),
或等依赖链模式清晰后单独设计。相关: 线性组合除 `AlphaComboEqual` 外还有 `AlphaLoad` (读外部 position) 等形式, 一并搁置。

## 已实现

- **`ops combo run <combo_dir>` 子命令** (`ops/services/combo/`, `ops/cli/combo.py`): 端到端编排 —
  形态自动判定 (有 `predict/` = 模型型) → [predict, 用 gsim venv] → 逐 stats 占位符注入 →
  gsim backtest → simsummary。无状态, 产物落 `<combo_dir>/runs/`。
  用法: `ops combo run <dir> --start <s> --end <e> [--predict-start <ws>] [--stats simple,bench,layer,opt]`。
  实现细节见 `ops/services/combo/CLAUDE.md`。

## 未来扩展

- `ops combo train` (滚动训练阶段, ops 用 OS 数据滚动训 checkpoint, 见"滚动训练"章节; `combo run` 已预留子命令结构)
- `H` (日期数) 动态计算注入 (现靠 ProdNpyLoad 默认 3900 = cc_2025; cc 变长时加)
- 多 combo 批量评估 + 排名 (对接 tmp/combo_doc.md 的滚动评估制度)
- 样本内优化器 API (研究员自助查样本内优化后表现, 绝不返回样本外)
- 跨 combo 依赖链支持 (见上"已知限制")

---

附: v0.1 的 MLflow Projects 设计 (MLproject/python_env.yaml/predict-backtest 分目录) 已废弃。
git 历史 48ab3dd / b336fe9 可查。废弃原因: mlflow 未走通 + 接口实测只需"最小集 + 占位符 config"。
