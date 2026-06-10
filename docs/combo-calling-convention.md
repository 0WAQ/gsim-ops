# Combo Calling Convention v0.1

QR 团队没有最新数据 (cc_2025+) 的访问权限,因此 combo 的完整 predict + backtest
必须在 ops 这边重跑。本文档定义 QR 提交 combo 时的最低交付约定 — 一份目录结构 +
若干必填文件,使 ops 这边可以"一行命令跑通",不需要每次手工识别 QR 塞了什么。

设计参考 [MLflow Projects](https://mlflow.org/docs/latest/projects.html) 和
[MLflow Models](https://mlflow.org/docs/latest/models.html),但**不强制依赖 mlflow 库** —
spec 自洽,用 mlflow 跑或自己写 50 行 wrapper 跑都行。

## 背景

QR 实际交付的是: **训练好的模型 + 推理代码 + gsim 配置**,不是 .npy。
原因是 QR 不能访问 cc_2025+,所以 .npy 必须由 ops 用 QR 的代码 + 最新 cc 重新生成。

## 目录结构

```
combo_<name>_v<ver>/                    # 顶层目录, 名字由 QR 自取
├── MLproject                           # 必填: 入口契约 (yaml)
├── python_env.yaml                     # 必填: 推理环境 (yaml)
├── combo.meta.json                     # 必填: 元信息 (唯一性 + 数据契约)
├── config.xml                          # 必填: gsim backtest 基准配置
├── predict.py                          # 必填: 推理入口脚本
├── models/                             # 必填: 模型 checkpoint
│   └── ...
├── README.md                           # 选填
├── output/                             # 选填: QR 自跑的历史 .npy (sanity check 用)
│   └── combo.npy
└── extras/                             # 选填: 训练代码 / 实验日志 / 备查
```

## 必填文件

### MLproject (入口契约)

按 MLflow Projects 格式书写。**必须**提供以下 entry_points:

| entry_point | 用途 | 输入 | 产物 |
|---|---|---|---|
| `predict` | 用最新 cc 跑推理, 输出全段 .npy | data_root, start, end, device, output_path | `combo.npy` |
| `backtest` | 用 .npy + config.xml 跑 gsim | combo_npy, config_xml, gsim_python, gsim_root, output_dir | `gsim/pnl/<alpha_id>` |
| `summarize` | pnl -> 报告 | pnl_dir, start, end | `summary.txt` |
| `main` | 默认: predict + backtest + summarize 一条龙 | (以上联合) | 全部 |

示例 (`combo_equal_raw_v23_self_mlp_v1_top600`):

```yaml
name: combo_equal_raw_v23_self_mlp_v1_top600
python_env: python_env.yaml

entry_points:
  predict:
    parameters:
      data_root: {type: string, default: "/datasvc/data/cc"}
      start: {type: string, default: "20200102"}
      end: {type: string, default: "20251231"}
      device: {type: string, default: "cpu"}
      output_path: {type: string}
    command: >
      python predict.py
      --data-root {data_root} --start {start} --end {end}
      --device {device} --output {output_path}

  backtest:
    parameters:
      combo_npy: {type: string}
      config_xml: {type: string, default: "config.xml"}
      gsim_python: {type: string, default: "/usr/local/gsim/.venv/bin/python"}
      gsim_root: {type: string, default: "/usr/local/gsim"}
      output_dir: {type: string, default: "gsim"}
    command: >
      {gsim_python} {gsim_root}/run.py {config_xml}

  summarize:
    parameters:
      pnl_dir: {type: string}
      start: {type: string, default: "20200102"}
      end: {type: string, default: "20251231"}
      gsim_python: {type: string, default: "/usr/local/gsim/.venv/bin/python"}
      gsim_root: {type: string, default: "/usr/local/gsim"}
    command: >
      {gsim_python} {gsim_root}/tools/simsummary.py
      -s {start} -e {end} {pnl_dir}

  main:
    parameters:
      data_root: {type: string, default: "/datasvc/data/cc"}
      start: {type: string, default: "20200102"}
      end: {type: string, default: "20251231"}
      device: {type: string, default: "cpu"}
    command: "bash run_all.sh"   # 内部串 3 个 entry, 接受同一组 env vars
```

约束:
- 所有路径必须可用 `${PROJECT_DIR}/...` (= `${ROOT}` in run_all.sh) 表达, 不允许 QR 的 home 目录绝对路径
- 不允许 "modify to your XXX.so" 这类待填占位符 — 提交时填实
- `device` 默认 `cpu` (ops 这边无 GPU); QR 自跑用 `cuda` 自己覆盖

### python_env.yaml (推理环境)

按 MLflow `python_env` 格式书写, 用于自动建一个干净的推理 env, 取代 `PYTHON=/home/lhw/miniconda3/envs/py312/bin/python` 这种硬编码。

```yaml
python: "3.12"
build_dependencies:
  - pip==24.0
dependencies:
  - lightgbm==4.3.0
  - torch==2.2.0
  - numpy==1.26.4
  - pandas==2.2.0
  - pyyaml
```

约束:
- 必须列出**所有** pip 依赖及精确版本 (no `latest`)
- 不允许 `-e <local-path>` (本地 editable install)
- 不允许引用 QR 私有 PyPI / git+ssh:... (除非 ops 这边能访问)

### combo.meta.json (元信息 + 唯一性)

```json
{
  "name": "combo_equal_raw_v23_self_mlp_v1_top600",
  "version": "0.1",
  "submit_date": "20260610",
  "author": "lhw",

  "npy": {
    "shape": [3657, 5484],
    "dtype": "float64",
    "date_range": ["20200102", "20251231"]
  },

  "data_deps": {
    "cc": {
      "fields": ["ashareeodprices.s_dq_adjclose", "..."],
      "required_until": "20241231"
    }
  },

  "external_artifacts": [],
  "stats_module": "StatsSimpleV5",
  "author_weight": "lhw:1.0",
  "notes": "0.5*lgbm_v23_raw + 0.5*self_mlp_v1_top600_raw"
}
```

**唯一性**: `(name, version, submit_date)` 三元组, 冲突时报错让 QR 改 version。

**data_deps** 字段是数据契约 — QR 声明这个 combo 用了哪些 cc 字段、需要数据延伸到什么日期。
ops 在跑前可以校验本地 cc 是否满足。

### config.xml (gsim backtest 基准)

QR 提供一个**可直接 `gsim run config.xml` 跑通**的基准配置, 不留占位符。

QR 提供的是基准, ops 保留**单方面修改权** (换 stats / 加 RiskOpt / 调 booksize / 改 universe 等),
修改不影响 .npy。

约束:
- 所有 `npydata="..."` 路径相对 config.xml 所在目录
- 不允许引用 QR 个人路径
- 如果用了外部 .so / .npy (例如 `AlphaOpRiskOpt20`, `/datasvc/data/cc/DmgrPwang_...`),
  必须在 `combo.meta.json` 的 `external_artifacts` 字段声明

### predict.py + models/ (推理代码 + checkpoint)

QR 自管目录结构。约束:
- `predict.py` 必须接受 `MLproject` 里 `predict` entry 声明的所有参数 (data_root, start, end, device, output)
- `--device cpu` 必须能跑通 (ops 这边无 GPU)
- 模型 checkpoint 全部在 `models/` 下, 不能从外部下载

## 数据访问契约

QR 在自己机器上准备 combo, 只能用截至 2024-12-31 的 cc 数据。
ops 在跑前**不会**把更新数据推给 QR。

理由: QR 如果能看到 2025 数据, 模型选择 / 超参可能反向调优出 look-ahead bias,
违背 out-of-sample 评估的目的。

ops 这边跑 `predict` 时, 用本地完整 cc (含 2025+), QR 的 predict.py 必须接受
任意 `--end` 日期, 不能假设 end=20241231。

## 提交流程

1. QR 在自己机器 准备 combo 目录, 满足本 spec
2. QR `rsync` 推送到 `ops:/mnt/storage/dropbox/qr/{yyyymmdd}/combo_<name>_v<ver>/` (路径 TBD)
3. ops 这边读 `combo.meta.json`, 校验三元组唯一 + python_env 字段完整 + config.xml 无占位符
4. ops 跑 `mlflow run <combo_dir>` (或等价 wrapper), 默认 `main` entry, 产出 .npy + pnl + summary
5. ops 把 summary + pnl 反馈给 QR, 决定是否进 `/production/signals/`

## 反检查清单 (ops 跑前自动校验)

| 检查项 | 不通过的处理 |
|---|---|
| `MLproject` 存在且 entry_points 含 predict/backtest/main | 拒收 |
| `python_env.yaml` 存在且 dependencies 段非空 | 拒收 |
| `combo.meta.json` 存在且三元组在 ops state 里不冲突 | 拒收 |
| `config.xml` 存在且无占位符 (grep "modify to your") | 拒收 |
| `predict.py` + `models/` 存在 | 拒收 |
| `external_artifacts` 声明的路径在 ops 这边可访问 | 拒收 |
| `data_deps.cc.fields` 在本地 cc 都有 | 警告 (允许 strict 模式拒收) |
| `device=cpu` 跑通 predict | 警告 |

## 参考实现

`tmp/combo_v0.1_20260605/` (lhw 提交) 已具备 80% 字段, 缺:
- `MLproject` (需新增, 抽 run_all.sh 的 env vars 进 parameters)
- `python_env.yaml` (需新增, 取代 `PYTHON=/home/lhw/miniconda3/...`)
- `combo.meta.json` 字段命名向 spec 对齐 (lhw 现有 `release_defaults.yaml` + `*.npy.meta.json` 已有等价信息)
- `config.xml` (v0.1 没带, 直接跑 `gsim_backtest.py`; spec 要求带)

补齐后即可 `mlflow run tmp/combo_v0.1_20260605 -e backtest -P combo_npy=...` 跑通。

## 未来扩展

- 多 combo 并行评估 / 自动对比报告: ops 这边加一个 `ops combo` 子命令封装, 进 redis state
- combo 上线: 测试通过 → `/production/signals/{author}/{combo_name}/` (单独 SOP, 不在本 spec)
- combo 之间相关性检查: 类似 alpha 的 corr 检查, 但 combo 数量级小, 可人工
- python_env 自动 build: 用 uv 或 conda-lock 锁定, 缓存复用
