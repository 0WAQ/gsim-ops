# combo service

`ops combo run <combo_dir>` — combo 测试端到端编排。把手动流程（替换 config 占位符 → 调 predict → gsim backtest → simsummary）封装成命令。无状态，纯跑测工具。

## 流程 (`combo.py` `ComboRunner`)

1. `Config.load()` → `data_root` 默认取 `config.nio_data_path` (cc_2025)，`--data-root` 可覆盖。
2. **形态自动判定**：`combo_dir/predict/` 存在 = 模型型，否则纯线性。
3. 模型型：`Runner.run_script(predict/predict.py, ...)` 用 **gsim venv** (`GSIM_VENV_PYTHON`，有 torch) 产出 `runs/predict_<start>-<end>/combo.npy`。纯线性跳过，产物落 `runs/backtest_<start>-<end>/`。
4. 逐 stats：读 `config.<stats>.xml` → `inject.py` 替占位符 → 写 `config.injected.xml` → `Runner.run_backtest` → `Runner.run_simsummary` → `Metrics`。
5. `printer` 打印各 stats 指标。

## 关键点

- **占位符** (`inject.py`)：`${RUN_DIR}` `${DATA_ROOT}` `${START}` `${END}` `${PNL_DIR}` `${CHECKPOINT_DIR}`。注入后断言无残留 `${...}`。
- **pnl 文件名按 config 的 Alpha id**（不是固定名）：`pnl_id_from_config` 取 Portfolio 下 `Alphas`（多腿/opt）优先否则 `Alpha`（单信号）的 `@id`。gsim 以它命名 pnl 文件。
- **两个 python 解释器**：跑 qr 的 predict 用 gsim venv（`Runner.run_script` 的 `python=`）；跑 gsim/simsummary 用 `config.python_path`（`run_backtest`/`run_simsummary` 内部）。
- **warmup**：`--predict-start` 应早于 `--start` ≥1 交易日，否则回测首日信号空（simple 静默空仓 / 优化器崩）。
- **未作 `mark_write` 写性声明(cli/common,S16)**：写的是 combo 自己的 `runs/`，非 root-owned alpha_src，无需 sudo 提权。

## 第一版不含 (见 docs/design/combo/calling-convention.md "未来扩展")

- train 阶段 / 滚动训练（`combo run` 预留了子命令结构，日后加 `combo train`）
- `H` 动态计算（现靠 ProdNpyLoad 默认 3900 = cc_2025；cc 变长再加）
- 多 combo 批量评估、跨 combo 依赖

## 规范

`docs/design/combo/calling-convention.md`（接口）/ `combo-submit-guide.md`（给 qr）/ `combo-train-spec.md`（train 接口）。
