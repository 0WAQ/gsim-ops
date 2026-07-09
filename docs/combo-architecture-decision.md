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

## 已解决问题 (2026-06-16 qr 回复 + 实测关闭, 原 A/B/C)

> qr 回复见 tmp/qr-answer.md;A/B/C 三个原未决项全部关闭。

### A. 性能: 逐日推理 vs 批量推理 — 关闭: 必须两段式 (批量 predict → .npy → 回测)

- **qr 答: 单日推理分钟级 (<10 分钟);全历史 (多模型+优化器) 20-30 分钟;CPU 可支持 (参数量不大),后续不排除要 GPU。**
- 实测吻合: 我们 cc_2025 全段 predict ~30-40 分钟。
- **结论**: 单日**分钟级**直接否决"gsim 回测循环内逐日实时推理 3900 天" (= 3900 × 分钟 = 数天)。
  → 回测**必须**先批量 predict 成 .npy 再喂 gsim;实盘每天只算一天 (分钟级可接受)。
  → 我们一直走的 "predict → .npy → backtest" 两段式是对的,不是 lhw 的随意选择,是性能决定的。
- 注: 这跟"combo = 模型型 alpha"不矛盾 —— 抽象上是 alpha,执行上因为单日太慢,
  回测时用 .npy 缓存 (gsim 自带 AlphaLoadFeat/ProdNpyLoad 读),实盘时逐日 generate。

### B. 范式普适性 — 关闭: 无状态前馈普适

- **qr 答: 当前都是离线训练 + 前馈推理;训练/推理分开 (不每天重训)。**
- **qr 答: 即使要 lookback,也是"cc 矩阵时间连续即可,从历史数据现拿现算",不显式传递跨日状态。**
  → 没有 LSTM 那种跨日 hidden state;给定 di,从 cc 读 di 往前的数据算,结果确定 = **无状态**。
- **结论**: combo = 模型型 alpha 的抽象成立,无范式例外。
- ⚠ 衍生注意: lookback 型 combo 推理某天要读该天往前 N 天的 cc。批量 predict 全段无碍;
  但做样本内优化器 API 时,要确保 lookback **不越界读到样本外** (隔离要求,见评估制度文档)。

### C. 实盘机制 — 关闭: 每天跑 gsim 出持仓, 回测/实盘同源

- **qr 答: 实盘"出持仓";认同"回测和实盘跑同一份推理代码,只换数据和日期"。**
- **结论**: 实盘 = 每天跑一次出持仓,与 combo 做成 gsim 可驱动模块、回测实盘同源完全吻合。
- 注: 实盘尚未真正上线 (wbai 2026-06-16: 还没增量),此处仍有落地自由度,但方向确定。

### Q4 外部依赖 (优化器) — 澄清

- **qr 答: `AlphaOpRiskOpt20` 是"勇哥的";目标尽量是公共大家都有的。**
- 即优化器 .so 公共可访问,combo 的 external_artifacts 检查降级为"只要 config 填实不留占位符"。

## 仍待落地 (制度层, 见 tmp/combo_doc.md 评估制度草案)

- **样本内优化器 API**: 研究员提交模型输出 → 返回样本内过优化器表现,但**绝不返回样本外**。
  最硬的一块: 优化器要可被研究员自助/反复调用,且优化器依赖的数据 (行业权重/市值/指数成分)
  也要截断到样本内,否则间接泄露样本外结构。
- **3 个月滚动评估**: 每轮隐藏 3 个月样本外,轮末并入下轮训练 (expanding window)。需多 combo 批量评估 + 排名。
- **防作弊**: 提交次数/相似版本管控,防试探隐藏测试集。


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
- MLflow Tracking/Registry: 暂不需要 (已有 ops state + gsim pnl;注 2026-07-07: state 后端已从 redis 迁 Postgres,结论不变)
- 结论: 借 MLflow Projects 的"环境/入口"格式, 不引入 MLflow 框架本身
