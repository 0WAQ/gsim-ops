# Combo 训练代码提交规范（滚动训练）

本规范定义滚动训练场景下，研究员需提交的训练代码 `train.py` 的接口约定。其余提交物（`predict.py`、`config.<stats>.xml`、目录结构等）见《Combo 提交规范》。

## 背景

研究员无样本外（`cc_2025+`）数据访问权限，无法训练样本外区间的模型。因此滚动训练由 `ops` 执行：研究员提交训练代码，`ops` 用样本外数据按时间滚动训出一系列 checkpoint，推理时按日期选用。

链路相应变为三段：

```
train（ops 滚动训 → 按日期 checkpoint） → predict（按日期选 checkpoint 推理） → backtest
```

`train.py` 仅在滚动训练场景必交；若研究员已提供全部所需 checkpoint，则无需此项。

## 接口

研究员在 `predict/` 下提供训练入口 `train.py`。`ops` 以不同 `--train-end` 多次调用，每次产出一个 checkpoint：

| 参数 | 含义 |
|---|---|
| `--data-root` | `cc` 数据根（`ops` 注入，含样本外区间） |
| `--train-end` | 训练数据截止日（`yyyymmdd`）。**仅可使用 ≤ 该日的数据训练** |
| `--out-dir` | checkpoint 输出目录（`ops` 注入） |

约束：

- 不得 hardcode 数据路径与日期，全部经参数传入。
- **严格遵守 `--train-end`**：训练只能读取截止日及之前的数据，不得触碰之后任何数据（含特征、标签、统计量）。这是样本外评估有效性的前提。
- 训练策略（回看窗口长度、扩展窗 / 滚动窗、超参等）由研究员在代码内部决定，`ops` 不干预。
- 须能在 `ops` 执行环境跑通。算力需求（CPU / GPU、耗时）请在交付时说明。

## checkpoint 命名

`train.py` 产出的 checkpoint 须按以下规则命名，`ops` 据此在推理时按日期选用：

```
<prefix>_<train-end>...        # 文件名第二段为 train-end 日期（yyyymmdd）
```

示例（`ops` 以四个季度末 `--train-end` 滚动调用的产出）：

```
<out-dir>/
├── model_20241231.pkl
├── model_20250331.pkl
├── model_20250630.pkl
└── model_20250930.pkl
```

推理时，`ops` 对每个交易日选用「截止日 ≤ 当日」中最新的一个 checkpoint：

```
推 2025Q1 → model_20241231   （该季度开始前最新）
推 2025Q2 → model_20250331
推 2025Q3 → model_20250630
推 2025Q4 → model_20250930
```

滚动粒度（年 / 季 / 月）由 `ops` 调用 `--train-end` 的次数决定，`train.py` 无需关心粒度，只需保证：一次调用、给定一个 `--train-end`、产出对应日期的 checkpoint。

## 调用示意

`ops` 侧滚动驱动（研究员无需实现循环）：

```bash
for te in 20241231 20250331 20250630 20250930; do
    python predict/train.py --data-root <cc> --train-end $te --out-dir <models>
done
```

随后由 `predict.py` 按日期选用这些 checkpoint 完成滚动推理。
