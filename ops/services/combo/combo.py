"""ops combo run — combo 测试端到端编排。

链路: [predict (模型型)] → 逐 stats 注入 config → gsim backtest → simsummary。
无状态: 不进 alpha state/锁; 产物全落 <combo_dir>/runs/。
形态自动判定: 有 predict/ = 模型型 (跑 predict→backtest); 无 = 纯线性 (直接 backtest)。

详见 docs/combo-calling-convention.md。
"""
from pathlib import Path

import xmltodict

from ops.infra.config import Config
from ops.infra.gsim.runner import Runner, BacktestError, ScriptError
from ops.core.metrics import Metrics
from ops.utils import printer

# combo 的 predict/train 脚本固定用 gsim venv (有 torch/lgbm), 区别于 ops 自己的 venv。
GSIM_VENV_PYTHON = "/usr/local/gsim/.venv/bin/python"

ALL_STATS = ["simple", "bench", "layer", "opt"]


def pnl_id_from_config(xml_text: str) -> str:
    """从注入后的 config 取顶层组合的 id —— gsim 以它命名 pnl 文件 (非固定名)。

    Portfolio 下 <Alphas> (多腿组合, 如 opt) 优先; 否则 <Alpha> (单信号, 如 simple)。
    """
    port = xmltodict.parse(xml_text)["gsim"]["Portfolio"]
    if "Alphas" in port:
        node = port["Alphas"]
    elif "Alpha" in port:
        node = port["Alpha"]
    else:
        raise ValueError("config Portfolio 下既无 Alphas 也无 Alpha")
    if isinstance(node, list):
        node = node[0]
    return node["@id"]


class ComboRunner:
    def __init__(self, combo_dir: Path, config: Config, *, start: str, end: str,
                 predict_start: str, data_root: Path, stats: list[str], device: str):
        self.combo_dir = combo_dir.resolve()
        self.config = config
        self.start = start
        self.end = end
        self.predict_start = predict_start
        self.data_root = data_root
        self.stats = stats
        self.device = device
        self.is_model_form = (self.combo_dir / "predict").is_dir()

    def run(self):
        printer.banner(f"combo run: {self.combo_dir.name}")
        printer.info(f"形态: {'模型型' if self.is_model_form else '纯线性'}  "
                     f"区间: {self.start}~{self.end}  data-root: {self.data_root}")

        prefix = "predict" if self.is_model_form else "backtest"
        run_dir = self.combo_dir / "runs" / f"{prefix}_{self.start}-{self.end}"
        run_dir.mkdir(parents=True, exist_ok=True)

        if self.is_model_form:
            self._run_predict(run_dir)

        results: dict[str, Metrics | None] = {}
        for st in self.stats:
            cfg_file = self.combo_dir / f"config.{st}.xml"
            if not cfg_file.exists():
                printer.warn(f"跳过 {st}: 无 config.{st}.xml")
                continue
            results[st] = self._run_one_stats(st, cfg_file, run_dir)

        self._report(results, run_dir)
        return results

    def _run_predict(self, run_dir: Path):
        printer.progress("predict ", f"{self.predict_start}~{self.end}")
        Runner.run_script(
            self.combo_dir / "predict" / "predict.py",
            ["--data-root", str(self.data_root),
             "--start", self.predict_start,
             "--end", self.end,
             "--device", self.device,
             "--output-dir", str(run_dir),
             "--out-name", "combo.npy"],
            python=GSIM_VENV_PYTHON,
            cwd=self.combo_dir,
            config=self.config,
        )
        npy = run_dir / "combo.npy"
        if not npy.exists():
            raise ScriptError("predict.py", f"未产出主信号 {npy}")
        printer.info(f"✔ predict → {npy}")

    def _run_one_stats(self, st: str, cfg_file: Path, run_dir: Path) -> Metrics | None:
        from .inject import inject

        stats_dir = run_dir / st
        pnl_dir = stats_dir / "pnl"
        for d in (pnl_dir, stats_dir / "positions", stats_dir / "checkpoint"):
            d.mkdir(parents=True, exist_ok=True)

        injected = inject(
            cfg_file.read_text(),
            run_dir=run_dir,
            data_root=self.data_root,
            start=self.start,
            end=self.end,
            pnl_dir=pnl_dir,
            checkpoint_dir=stats_dir / "checkpoint",
        )
        injected_path = stats_dir / "config.injected.xml"
        injected_path.write_text(injected)

        printer.progress("backtest: ", st)
        try:
            Runner.run_backtest(injected_path, self.config)
        except BacktestError as e:
            printer.error(f"✘ {st} backtest 失败: {repr(e)[:300]}")
            printer.warn("  若首日信号为空, 检查 warmup: --predict-start 应早于 --start ≥1 交易日")
            return None

        pnl_file = pnl_dir / pnl_id_from_config(injected)
        if not pnl_file.exists():
            printer.error(f"✘ {st}: 未找到 pnl {pnl_file}")
            return None
        metrics = Runner.run_simsummary(pnl_file, self.config)
        if metrics is None:
            printer.warn(f"⚠ {st}: simsummary 解析失败 (pnl 在 {pnl_file})")
        else:
            printer.info(f"✔ {st}: {metrics}")
        return metrics

    def _report(self, results: dict[str, Metrics | None], run_dir: Path):
        printer.banner("结果")
        for st, m in results.items():
            line = str(m) if m else "(无指标)"
            printer.highlight(f"{st:8} {line}")
        printer.info(f"产物: {run_dir}")
        printer.bottom()


def run_combo(args):
    config = Config.load(args.config_path)
    combo_dir = Path(args.combo_dir)
    if not combo_dir.is_dir():
        printer.error(f"combo 目录不存在: {combo_dir}")
        return

    data_root = Path(args.data_root) if args.data_root else config.nio_data_path
    predict_start = args.predict_start or args.start
    stats = [s.strip() for s in args.stats.split(",") if s.strip()]

    ComboRunner(
        combo_dir, config,
        start=args.start, end=args.end, predict_start=predict_start,
        data_root=data_root, stats=stats, device=args.device,
    ).run()
