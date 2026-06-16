# Combo 架构决策记录 (Architecture Decision Record)

> 状态: 讨论中 / 待 qr 对齐 + 性能实测。本文记录 2026-06 关于 combo 接入的关键认知与决策,
> 防止上下文丢失。结论建立在分析 + 推导上,**lhw 的代码只是一个样本,不是规范**。

## 背景与问题

QR 团队没有 cc_2025+ 数据访问权限,提交的 combo 需要 ops 这边用最新数据测试。
qr 提交没有统一形式 (各种 config / 复杂代码),需要约定一套 Calling Convention。

**数据隔离动机**: 不能把 2025+ 数据开放给 qr,否则模型选择/超参会反向过拟合 (look-ahead)。

## 核心认知 (按讨论顺序)

### 1. combo 本质 = 一个"算法是模型"的 Alpha

- 普通 Alpha: `generate(di)` 里是人写的固定公式,如 `rank(close/delay(close,5))`
- Combo: `generate(di)` 里是 `model.predict(X)` —— 把今天的 feature X 喂进训练好的模型 f(X; θ)
- **两者对回测 (Stats) 完全一样**: Stats 只看每天的信号数值,不关心信号怎么来的
- 区别只在 `generate` 内部: 简单算术 vs 训练出来的非线性模型函数

### 2. 训练 / 推理界限

| | 训练 | 推理 |
|---|---|---|
| 做什么 | 得到模型参数 θ | 用 θ 算出信号 model.predict(X) |
| 谁做 | qr (黑箱, ops 不接管) | ops (在 cc_2025 上跑) |
| 状态 | 有 (梯度下降) | 通常无 (前馈) |

交付界面 = ① 模型权重 (按日期命名的 checkpoint) + ② feature 清单 (用 cc 哪些字段) + ③ 模型结构代码 (能把权重组装成可调用的函数)

### 3. 信号供给两种模式 (与 alpha 完全一致)

```
              ┌─ 实时计算: generate(di) 当场算 ─┐
信号产生 ──────┤                                ├──→ Stats 模块 → pnl/指标
(Alpha/Combo) └─ 加载预算好的 .npy ────────────┘     (回测本体)
```

- `.npy` 不是交付物,是 `generate` 的**缓存** (推理贵 / 要复用多组回测参数时才用)
- qr **不该**只交 .npy (= v0.2_pre 被否的禁止形态,因为它只到 2024,拿不到 2025)
- 实盘 = `generate(today)` 实时算今天;回测 = `generate(di)` 跑历史或读缓存。同一份代码,换 data_root + 日期范围

### 3.5 数据来源 + 外部依赖 (2026-06-16 wbai 澄清)

- **feature 来源**: 要么是 cc (`/datasvc/data/cc/...`),要么是因子库。两类我这边都有,combo 的 feature 清单只会落在这两处。
- **外部依赖 (优化器等 .so)**: 是**公共的,大家都能访问** (如 `AlphaOpRiskOpt20`)。
  → 之前 v0.2_pre config 里 `"modify to your AlphaOpRiskOpt20.so"` 那个占位符,不是"qr 有我没有",
    而是 qr 留的待填项;实际 .so 是公共的,我这边能拿到。
  → external_artifacts 检查降级: 公共 .so 不算阻塞项,只要 config 填实 (不留占位符) 即可。

### 4. 历史断层 = combo 与 gsim 割裂的根因 (★ 关键)

gsim 是线性模型时代设计的:

```
gsim 设计时 (线性时代)              现在 (非线性时代)
几十个 alpha (人写公式)             成千上万 feature
   → Combo: Σ wᵢ·alphaᵢ            → model.predict(X), 几百万参数非线性
   (加权组合, 权重可解释)            (端到端, 不可解释)
```

概念层级错位:

| gsim 概念 | 线性时代含义 | 现代 ML 对应 | 错位 |
|---|---|---|---|
| Alpha | 一个人造弱信号 | 一个 feature | feature 有几千个 |
| Combo | 给 alpha 加权 | 一个模型 | 模型不是加权, 是任意函数 |

lhw 绕开 `<Alphas><Alpha>×N<Combo>` 结构、直接 predict 成 .npy 再 `ProdNpyLoad`,
**根因是那个为线性设计的结构表达不了"几千 feature + 一个 MLP"**,不是图省事。

### 5. gsim 里的"两个 Combo" (已澄清)

- **代码里的 `gsim/combo/ComboBase`**: `combine(di)` 把多个**已算好的 alpha** 加权 (Σ wᵢ·alphaᵢ)
- **xsd 里的 `<Combo>` 模块**: 就是上面那个的 schema 声明,`<Alphas combo="xxx">` 引用它
- 二者是**同一个东西的两面**。"我们没用过后者" = 没在 config 里用 `<Combo>` 标签声明自定义组合器,一直用默认 `AlphaComboEqual`
- **关键: 这个 Combo (组合器/信号组合层) 跟我们要做的"模型型 combo" (信号产生层) 不是一回事**
- 我们要做的模型信号,对应的是 **Alpha (AlphaBase)**,不是这个 Combo (ComboBase)。撞名了,规范里要说清

### 6. gsim 模块加载机制 (查证结果)

- `gsim/utils/utils.py:load_module_src` 用 `importlib.util.spec_from_file_location` 从 xml 里
  `module="xxx.py"` 指定的任意路径加载,找继承基类的 class 实例化
- 参数全走 xml 标签属性 + `cfg.getAttribute*` 传入
- → combo 模块拿模型路径/feature清单/checkpoint目录,跟因子一样在 xml 标签写属性即可
- gsim venv 有 torch 2.8 / lightgbm 4.6 / joblib,`import torch` 在模块里可用
- **gsim 已内置 `alpha_load.py` / `alpha_load_feature.py` (注释 "used for combo")**: 继承 AlphaBase,
  `generate(di)` 从文件/feature 读预算好的信号 —— 即 lhw `ProdNpyLoad` 想干的事 gsim 自带了

## 当前倾向的方案

**combo = 继承 `AlphaBase` 的"模型型 alpha"模块** (不是继承 ComboBase):

```python
class MyModelCombo(AlphaBase):
    def __init__(self, cfg):
        AlphaBase.__init__(self, cfg)
        self.model_dir = cfg.getAttributeString('modelDir')      # xml 传路径
        self.features  = load_feature_list(cfg.getAttributeString('featureCsv'))
    def generate(self, di):
        model = pick_checkpoint(self.model_dir, di)              # 按日期选, 防 look-ahead
        X = assemble_features(self.features, di)
        self.alpha[valid_idx] = model.predict(X)[valid_idx]      # 推理
```

- 复用 gsim 的数据访问 (dr.getData/niodatapath)、universe、Stats、撮合
- gsim 的 Combo 层 (ComboBase) 在非线性时代退化为"可选的最后加权" (如 0.9×模型 + 0.1×pwang)
- 数据隔离: niodatapath 由 ops 控制指向 cc_2025,qr 碰不到
- 实时模式 (实盘 + 单配置回测) vs 缓存模式 (.npy + 多配置回测) 按场景选

qr 交付: combo 模块代码 (实现 generate) + 模型权重 (按日期 checkpoint) + feature 清单 + (可选) gsim config。
qr **不交**: .npy / 训练代码 / 数据。

## 未决问题 (阻塞最终拍板)

### A. 性能: 逐日推理 vs 批量推理 (★ 需实测)

- gsim Alpha 是"每天一个轻量公式"设计,逐日 `generate(di)` 串行
- 几千 feature 的 MLP 塞进 `generate(di)` 每天前馈,回测 3900 天可能比一次性批量推理慢得多
  (批量能向量化 / GPU 整段并行) —— lhw 先 predict 整段 .npy 可能正是这个性能原因
- **张力: 回测要批量 (快), 实盘每天只算一天 (逐日没问题)**
- **待测: 一个几千 feature 的 MLP, 逐日 generate(di) 推理一天要多久?**
  - 毫秒级 → 当 Alpha 无问题
  - 秒级 → 回测 3900 天 = 小时级, 必须批量缓存
  - 用 lhw 的 mlp 测单日耗时 × 3900 估全段成本

### B. 范式普适性: 需 qr 对齐

- 当前认知全部基于 lhw 一人的 frozen-inference 范式 (离线训练出按日期 checkpoint, 推理只前馈)
- 风险: 别的 qr 可能不同 —— 在线学习/每日重训 (训练推理焊死) / 时序状态模型 (LSTM 跨日 hidden state)
- **待问 qr**: "是不是都遵循'离线训练出按日期 checkpoint, 推理只做前馈、不在线更新'? 有没有谁推理需要跨日状态或每天重训?"

### C. 实盘机制: ops 这边未知 (2026-06-16 部分澄清)

- 147 实盘现在到底怎么每天产出信号? 跑一次 gsim 出当日持仓? 还是独立推理服务?
- **wbai 答 (2026-06-16): 实盘目前还没有增量 (尚未上线), 预期是每天跑一次。**
  → 即"每天跑一次"模式,与 combo 做成 gsim 模块、回测/实盘同一份代码同一入口的设想吻合。
  → "每天跑一次" = 逐日 generate(today) 只算一天,逐日推理对实盘**没有**性能问题 (性能压力只在回测全段重放时,见 A)。
- 仍待明确: "每天跑一次"具体是不是跑 gsim (而非独立服务)? 由于实盘未上线,此处有设计自由度,
  倾向定为"实盘每天跑一次 gsim,combo 作为 gsim 模块被 generate(today) 驱动"。

## 已完成的工作 (tmp/combo_v0.1_20260605/, gitignore 不进库)

- spec 草案: `docs/combo-calling-convention.md` (已 commit 48ab3dd)
- 把 lhw v0.1 改造为 MLflow Projects 形态 (MLproject + python_env.yaml + combo.meta.json + predict/backtest 目录)
- **验证: mlflow run backtest 与 lhw 自跑 bit-exact 一致** (5年×3mode, pnl sha256 逐一相同)
- **验证: predict 用 cc_2025 跑到 2025 out-of-sample 成功** (CPU, 短窗口)
- 注: MLflow 这条路是"把 lhw 现状包装可跑"的验证; 上面第 4 节的认知出现在之后,
  说明**更本质的方向是 combo = 模型型 alpha 进 gsim, 而非用 MLflow 包装外部 predict**。
  两条路并存待决: MLflow-wrap (gsim 外) vs AlphaBase-module (gsim 内)

## MLflow 参考 (业界对位)

- MLflow Projects (MLproject + python_env.yaml): 入口契约 + 环境约定, 可借鉴格式
- MLflow Models (MLmodel flavors/signature): 借思路不抄结构
- MLflow Tracking/Registry: 暂不需要 (已有 redis state + gsim pnl)
- 结论: 借 MLflow Projects 的"环境/入口"格式, 不引入 MLflow 框架本身
