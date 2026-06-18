# Combo 提交规范

本规范定义 `combo` 提交给 `ops` 测试的目录结构与接口约定。`ops` 使用最新数据（含样本外区间）重跑 `predict` 与 `backtest`，返回各项指标。

## 背景

研究员无样本外（`cc_2025+`）数据访问权限。`combo` 推理须由 `ops` 用最新数据重跑后回测，否则无法获得样本外表现。因此提交物为**模型、推理代码与回测配置**，而非预先算好的信号。

## 链路

```
predict（模型在 feature 上算信号 → .npy） → backtest（gsim 跑 .npy → pnl）
```

`predict` 由 `ops` 用最新数据重跑，`backtest` 使用提交的 `config`。

## 提交目录

```
Combo{UnixId}{ComboName}/      命名同 alpha，无分隔，如 CombolhwEqualV23
├── predict/                   推理代码，入口 predict.py（纯线性形态无此项）
├── models/                    模型权重 + feature 清单（纯线性形态无此项）
├── config.simple.xml          回测配置，每个 stats 一份
├── config.bench.xml
├── config.layer.xml
├── config.opt.xml
└── runs/                      产物目录，提交时留空
```

身份信息（作者、`combo` 名）由目录名表达。模型型的数据依赖由 `models/` 下的 `feature_names.csv` 描述；纯线性的数据依赖由 `config` 中各 `npydata` 引用表达。无需额外元信息文件。

## 三种形态

| 形态 | 信号来源 | `predict` / `models` | 接收 |
|---|---|---|---|
| 模型型 | 模型推理 | 必交 | 是 |
| 纯线性 | `config` 内 `<Alphas combo>` 线性组合现成因子，无模型 | 不需要 | 是 |
| 只交 `.npy` | 算好的成品 `.npy`，无推理代码 | 无 | 否 |

只交 `.npy` 不予接收：成品仅覆盖样本内区间，`ops` 无法获得样本外段。纯线性 `combo` 无模型、信号即现成因子的线性组合，无需 `predict`，直接回测。

## 接口一：predict 调用（模型型）

`ops` 调用方式：

| 参数 | 含义 |
|---|---|
| `--data-root` | `cc` 数据根（`ops` 注入，可能为 `cc_2025`） |
| `--start` / `--end` | 推理区间 |
| `--device` | `cpu` / `cuda`（`ops` 回测机无 GPU，须支持 `cpu`） |
| `--output-dir` | 产物目录 |

约束：

- 不得 hardcode 数据路径与日期。参数全部由外部传入——同一代码用于回测（全段）与实盘（当天），仅区间不同。
- 主产物为一个名为 `combo.npy` 的 `.npy`，shape 为 `(数据日期数, 股票数)`，形状由 `data-root` 推导，不得写死。`predict` 内部可另落中间产物（如各子模型的输出），`ops` 不使用。
- `config` 须引用 `${RUN_DIR}/combo.npy`。
- `--device cpu` 须能跑通。

## 接口二：回测配置

每个 `stats` 一份 `config.<stats>.xml`，提交完整 `config`，仅将以下环境字段写为占位符，由 `ops` 注入：

| 占位符 | `ops` 注入 |
|---|---|
| `${RUN_DIR}` | `predict` 产物目录，作 `npy` 路径前缀（仅模型型使用） |
| `${DATA_ROOT}` | 数据根 |
| `${START}` / `${END}` | 回测区间 |
| `${PNL_DIR}` | `pnl` 输出目录 |

模型型使用全部四类占位符；纯线性无 `predict` 产物，不使用 `${RUN_DIR}`，信号经 `${DATA_ROOT}` 引用现成因子。

策略部分（后处理算子、优化器及参数、对标指数、组合权重）由研究员自行决定，`ops` 不修改。优化器参数由研究员自行调试。

`npy` 路径仅占目录前缀，文件名与子目录由研究员自定，以避免硬编码并支持任意数量的信号分量。`config` 中不得出现绝对文件路径、个人目录、待填占位符；引用的外部 `.so`（如优化器）须为公共可访问。

## 参考模板

### 模型型

`predict` 入口实现以下签名（`--device` 默认值建议 `cpu` 或自动探测，确保无 GPU 环境可跑）：

```python
# predict/predict.py
parser.add_argument("--data-root", ...)   # ops 注入
parser.add_argument("--start", ...)
parser.add_argument("--end", ...)
parser.add_argument("--device", default="cpu")
parser.add_argument("--output-dir", ...)
parser.add_argument("--out-name", default="combo.npy")   # 主产物固定名
```

`config` 引用 `predict` 产物，以 `${RUN_DIR}` 前缀 + 相对路径（若有多路分量信号，各自一行）：

```xml
<Alpha id="blend" module="ProdNpyLoad" npydata="${RUN_DIR}/combo.npy" weight="1.0">
  <Operations>
    <Operation module="AlphaOpDecay" days="2"/>
    <Operation module="AlphaOpPower" exp="1.0"/>
    <Operation module="AlphaOpIndNeut" group="sector"/>
  </Operations>
</Alpha>
```

### 纯线性

无 `predict`、无模型。`config` 内以 `AlphaComboEqual` 加权组合若干 `cc` 现成因子，每个因子一个权重，以 `${DATA_ROOT}` 前缀引用：

```xml
<Alphas id="LinearCombo" universeId="ALL_TRD" combo="AlphaComboEqual" ...>
  <Alpha id="DAREV" module="ProdNpyLoad" npydata="${DATA_ROOT}/equ_factor_af/equ_factor_af.DAREV.npy" weight="0.000064">
    <Operations>
      <Operation module="AlphaOpNormalize"/>
      <Operation module="AlphaOpWinsorize" std="8.0"/>
    </Operations>
  </Alpha>
  <!-- 其余因子，每个一行，各带 weight -->
</Alphas>
```

## stats 约定

| `config` | `Stats` | 说明 |
|---|---|---|
| `config.simple.xml` | `StatsSimpleV5` mode 0 | 纯多空 |
| `config.bench.xml` | mode 1 + `index_ret` | 对标指数超额 |
| `config.layer.xml` | mode 2 + `index_ret` + `thres` | 分层 |
| `config.opt.xml` | `StatsOptV5` + 优化器 | 风险优化后 |

`simple` / `bench` / `layer` 共享信号与后处理，仅 `<Stats>` 行不同；`opt` 为独立结构（含组合容器与优化器）。

## 产物

```
runs/predict_<start>-<end>/    模型型：一次 predict
├── combo.npy                  predict 信号
└── <stats>/                   每个 stats 一个目录
    ├── pnl/
    ├── summary.txt
    └── config.injected.xml    注入后的实际 config
```

纯线性形态无 `predict` 产物，该层为 `runs/backtest_<start>-<end>/`，其下直接为各 `<stats>/` 目录，无 `combo.npy`。

同一信号被多个 `stats` 复用：模型型下，一次 `predict` 的 `combo.npy` 供其全部 `stats` 回测，换 `stats` 无需重跑 `predict`。

## 注意事项

- **warmup**：`predict` 起点须早于回测起点至少若干交易日，以覆盖后处理 warmup 与一日延迟。否则回测首日信号为空——`simple` 静默空仓，优化器报错。
- **数据隔离**：研究员准备 `combo` 仅可使用样本内数据。接触样本外会导致反向过拟合，违背评估目的。
