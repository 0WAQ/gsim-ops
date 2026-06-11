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
├── MLproject                           # 必填: 入口契约 (yaml) — 必须在顶层 (mlflow 硬要求)
├── python_env.yaml                     # 必填: 推理环境 (yaml)
├── combo.meta.json                     # 必填: 元信息 (唯一性 + 数据契约)
├── README.md                           # 选填
├── run_all.sh                          # 选填: 便捷脚本 (端到端 predict + backtest)
├── predict/                            # 必填: 推理代码 (入口 predict.py + helpers)
│   ├── predict.py                      #   入口, 接受 MLproject predict entry 的参数
│   └── *.py                            #   helpers (predict_common / predict_lgbm / ...)
├── backtest/                           # 二选一: gsim 回测代码 (动生 xml 方式, 见下)
│   ├── gsim_backtest.py                #   入口, 动生 config 并调 gsim
│   └── *.py                            #   gsim Alpha loader / helpers
├── config.xml                          # 二选一: 静态 xml (见 Backtest config 一节)
├── models/                             # 必填: 模型 checkpoint
│   └── ...
├── output/                             # 选填: QR 自跑的历史 .npy (sanity check 用)
│   └── combo.npy
└── extras/                             # 选填: 训练代码 / 实验日志 / 备查
```

> **目录约定**: 推理代码进 `predict/`,回测代码进 `backtest/`,与 MLproject 的
> `predict` / `backtest` entry_points 命名对齐。只有 `MLproject` / `python_env.yaml` /
> `combo.meta.json` / `README.md` 留在顶层。脚本用 `Path(__file__).resolve().parent.parent`
> (而非 `.parent`) 回溯 combo 根目录,以正确定位 `models/` 和 `output/`。

## 必填文件

### MLproject (入口契约)

按 MLflow Projects 格式书写。**必须**提供以下 entry_points:

| entry_point | 用途 | 输入 | 产物 |
|---|---|---|---|
| `predict` | 用最新 cc 跑推理, 输出全段 .npy | data_root, start, end, device, output_path | `combo.npy` |
| `backtest` | 用 .npy + (静态 xml 或 Python 动生) 跑 gsim | combo_npy, gsim_python, gsim_root, output_dir | `gsim/pnl/<alpha_id>` + dumped xml |
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
      python predict/predict.py
      --data-root {data_root} --start {start} --end {end}
      --device {device} --output {output_path}

  backtest:
    parameters:
      combo_npy: {type: string}
      gsim_python: {type: string, default: "/usr/local/gsim/.venv/bin/python"}
      gsim_root: {type: string, default: "/usr/local/gsim"}
      output_dir: {type: string, default: "gsim"}
    command: >
      python backtest/gsim_backtest.py
      --combo {combo_npy} --output-dir {output_dir}
      --gsim-python {gsim_python} --gsim-root {gsim_root}

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

### Backtest config (静态 xml 或 Python 动生)

允许两种方式:

**方式 A 静态 xml**: 顶层放 `config.xml`,`gsim run config.xml` 直接可跑。
适合: backtest mode 单一, 配置稳定的 combo (例如 v0.2_pre 风格)。

**方式 B Python 动生**: 没有静态 `config.xml`,而是在 `gsim_backtest.py` (或类似脚本) 里
按 mode (simple/bench/layer/...) 在运行时拼装 xml,写到 `<output_dir>/<mode>/config.xml` 后调 gsim。
适合: 同一份 combo 想同时跑多个评估视角的场合 (例如 v0.1_20260605 风格)。

**硬约束** (两种方式都必须满足):

1. backtest 跑完后, `<output_dir>/` 下**必须**存在最终用的 xml (一份或多份)
2. 这份 dumped xml 必须可以**独立**用 `gsim run <dumped.xml>` 重跑出**等价** pnl
   (= QR 不能把任何 backtest 关键逻辑藏在动生代码里, 使 dump 之后跑不出来)
3. 不允许 "modify to your XXX.so" 这类待填占位符 — 提交时填实
4. 所有 `npydata="..."` 路径相对 dumped xml 所在目录或显式声明为绝对路径
5. 引用了外部 .so / .npy (例如 `AlphaOpRiskOpt20`, `/datasvc/data/cc/DmgrPwang_...`)
   必须在 `combo.meta.json` 的 `external_artifacts` 字段声明

ops 保留**单方面修改 dumped xml 的权利** (换 stats / 加 RiskOpt / 调 booksize / 改 universe 等),
修改后再 `gsim run` 一次, 不影响 .npy 本身。

### predict/ + models/ (推理代码 + checkpoint)

推理代码进 `predict/` 子目录 (入口 `predict/predict.py` + helpers)。约束:
- `predict/predict.py` 必须接受 `MLproject` 里 `predict` entry 声明的所有参数 (data_root, start, end, device, output)
- `--device cpu` 必须能跑通 (ops 这边无 GPU)
- 模型 checkpoint 全部在 `models/` 下, 不能从外部下载
- 脚本回溯 combo 根目录时用 `Path(__file__).resolve().parent.parent` (因为脚本在 `predict/` 子目录里)

## 禁止形态

以下提交形态 **ops 直接拒收**,原因都是无法复现 / 无法服务 ops 用 cc_2025+ out-of-sample
评估的核心目的:

| 禁止形态 | 例子 | 拒收理由 |
|---|---|---|
| 只交 `.npy` + `config.xml`, 缺 `predict.py` / `models/` | `tmp/combo_v0.2_pre/` | ops 无法重 predict, 拿不到 2025+ 段 |
| `MLproject` 里 `command` 引用 QR 个人路径 (`/home/lhw/miniconda3/...`) | lhw v0.1 现有 `run_all.sh` | ops 这边没有这个 env |
| `python_env.yaml` 缺失或依赖未固定版本 | — | 重现性无保障 |
| `config.xml` 或动生 xml 含占位符 (`"modify to your XXX.so"`) | v0.2_pre 现有 `AlphaOpRiskOpt20` 那一行 | 提交即未完工 |
| `external_artifacts` 声明了但路径在 ops 这边不可访问 | — | 跑不起来 |
| 模型从外部 URL 下载 (`wget xxx` in predict.py) | — | 不可复现, 不可审计 |

## 数据访问契约

QR 在自己机器上准备 combo, 只能用截至 2024-12-31 的 cc 数据。
ops 在跑前**不会**把更新数据推给 QR。

理由: QR 如果能看到 2025 数据, 模型选择 / 超参可能反向调优出 look-ahead bias,
违背 out-of-sample 评估的目的。

ops 这边跑 `predict` 时, 用本地完整 cc (含 2025+), QR 的 predict.py 必须接受
任意 `--end` 日期, 不能假设 end=20241231。

## 提交流程

1. QR 在自己机器 准备 combo 目录, 满足本 spec
2. QR `rsync` 推送到 ops 上的 dropbox (具体路径 TBD, MVP 阶段手交)
3. ops 这边读 `combo.meta.json`, 校验三元组唯一 + python_env 字段完整 + xml 无占位符
4. ops 跑 `mlflow run <combo_dir>` (或等价 wrapper), 默认 `main` entry, 产出 .npy + pnl + summary
5. ops 把 summary + pnl 反馈给 QR, 决定是否进 `/production/signals/`

## 反检查清单 (ops 跑前自动校验)

| 检查项 | 不通过的处理 |
|---|---|
| `MLproject` 存在且 entry_points 含 predict/backtest/main | 拒收 |
| `python_env.yaml` 存在且 dependencies 段非空 | 拒收 |
| `combo.meta.json` 存在且三元组在 ops state 里不冲突 | 拒收 |
| 顶层 `config.xml` 存在 OR `MLproject` 的 backtest entry 能跑出 dumped xml | 拒收 |
| 静态 xml 或 dumped xml 无占位符 (`grep "modify to your"`) | 拒收 |
| `predict.py` + `models/` 存在 | 拒收 |
| `external_artifacts` 声明的路径在 ops 这边可访问 | 拒收 |
| `data_deps.cc.fields` 在本地 cc 都有 | 警告 (允许 strict 模式拒收) |
| `device=cpu` 跑通 predict | 警告 |
| dumped xml 独立 `gsim run` 可重放出等价 pnl | 警告 (抽检, 不每次跑) |

## v0.1 改造记录 (tmp/combo_v0.1_20260605/)

lhw 的 v0.1 已具备约 80% 字段。ops 这边已直接补齐并改造为本 spec 的参考实现
(不依赖 QR 重新提交),下表是改造前后对照:

| 项 | 改造前 | 改造后 (已完成) |
|---|---|---|
| `MLproject` | 无, 入口只有 `run_all.sh` env-vars 参数化 | 已新增, entry_points: predict/backtest/summarize/main, 参数对齐两个入口脚本 CLI |
| `python_env.yaml` | `run_all.sh` 硬编码 `PYTHON=/home/lhw/miniconda3/envs/py312/bin/python` | 已新增: python 3.12 + lightgbm/torch/joblib/numpy/pandas/pyyaml; `run_all.sh` 改为 `PYTHON=${PYTHON:-python}` |
| `combo.meta.json` | 信息散落在 `configs/release_defaults.yaml` + 3 份 `output/*.npy.meta.json` | 已新增, 字段按 spec, 三元组 `(combo_equal_raw_v23_self_mlp_v1_top600, 0.1, 20260605)` |
| 目录结构 | 7 个 .py 全平铺在顶层 | 已重组: 推理进 `predict/`, 回测进 `backtest/`; `package_root()` 等改用 `.parent.parent` |
| `predict.py --device` 默认 | 默认 `cuda` (ops 无 GPU) | MLproject `predict`/`main` entry default 改为 `cpu`; 脚本本体不动 |
| 静态 config.xml | 无, `gsim_backtest.py` 运行时动生 simple/bench/layer 三份 | 走方式 B (Python 动生), 已验证 dumped xml 落在 `<output_dir>/` 下 |
| 路径硬编码 (lhw home) | 3 个 `.npy.meta.json` 里 `out` / `model_dir` 是 `/mnt/storage/dropbox/lhw/...` | 保留作 lhw 自跑历史快照, 不参与 ops 跑流程, 不改 |

**等价性验证**: 改造后 `mlflow run -e backtest` 全段 5 年 × 3 mode (simple/bench/layer)
的 pnl 与 lhw 自跑 `output/gsim_raw_stats/` **bit-exact 一致** (sha256 逐一比对),
summary.txt 完全相同,dumped xml 仅路径字段不同。重组目录后 4 天 smoke test 复测
pnl 仍 bit-exact。

跑法:

```bash
# 单跑 backtest (用 lhw 已 predict 好的 .npy)
mlflow run tmp/combo_v0.1_20260605 --env-manager local -e backtest \
  -P combo_npy=$(pwd)/tmp/combo_v0.1_20260605/output/combo_equal_raw_v23_self_mlp_v1_top600.npy

# 端到端 (重 predict 到 2025+ + backtest + summarize)
mlflow run tmp/combo_v0.1_20260605 -e main \
  -P end=20260131 -P device=cpu
```

## 未来扩展

- 多 combo 并行评估 / 自动对比报告: ops 这边加一个 `ops combo` 子命令封装, 进 redis state
- combo 上线: 测试通过 → `/production/signals/{author}/{combo_name}/` (单独 SOP, 不在本 spec)
- combo 之间相关性检查: 类似 alpha 的 corr 检查, 但 combo 数量级小, 可人工
- python_env 自动 build: 用 uv 或 conda-lock 锁定, 缓存复用
- dropbox 路径正式化: 等 3-5 个 combo 跑通后, 类比 alpha dropbox `/{author}/{yyyymmdd}/{combo_name}/`
