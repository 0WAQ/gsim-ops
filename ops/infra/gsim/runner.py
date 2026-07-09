import re
import subprocess
from pathlib import Path

from ops.core.metrics import Metrics
from ops.utils.log import logger

from ..config import Config


class BacktestError(Exception):
    """gsim 回测进程失败(非零退出 / 超时)。"""


class ScriptError(Exception):
    """qr 自带脚本 (predict.py / train.py) 执行失败。"""
    def __init__(self, script: str, stderr: str):
        self.script = script
        super().__init__(f"{script} failed: {stderr[:1000]}")


def resolve_bcorr_pools(config: Config, discovery_method: str | None) -> list[Path]:
    """按因子来源返回 bcorr 对比池。

    automated / manual 各自只和同类因子比;来源未知 (legacy 因子无此字段) 回退到
    全库比较 (pnl_prod 生产池 + pnl_alphalib 全库),与分类前的旧行为一致。
    """
    if discovery_method == "automated":
        return [config.pnl_automated]
    if discovery_method == "manual":
        return [config.pnl_manual]
    return [config.pnl_prod_path, config.pnl_alphalib]


class Runner:
    @staticmethod
    def run_bcorr(pnl_file: Path, config: Config,
                  pools: list[Path] | None = None) -> list[tuple[str, float]] | None:
        """对 pools 里的每个 pnl 目录各跑一次 bcorr,合并结果。

        pools 缺省为 [pnl_prod_path, pnl_alphalib](全库比较,旧行为)。分类比较由
        调用方传 resolve_bcorr_pools() 的结果。任一目录 bcorr 失败即返回 None。
        """
        if pools is None:
            pools = [config.pnl_prod_path, config.pnl_alphalib]
        try:
            corrs: list[tuple[str, float]] = []
            for pool in pools:
                result = subprocess.run(
                    [config.bcorr_script, str(pnl_file), str(pool)],
                    capture_output=True,
                    text=True,
                    timeout=config.timeout
                )
                if result.returncode != 0:
                    logger.warning("bcorr (vs {}) rc={} stderr={!r}",
                                   pool, result.returncode, result.stderr[:500])
                    return None
                for line in result.stdout.strip().split('\n'):
                    match = re.match(r"^(\S+)\s+([-\d.]+)", line.strip())
                    if match:
                        corrs.append((match.group(1), float(match.group(2))))

            return corrs

        except Exception:
            logger.exception("run_bcorr failed pnl={}", pnl_file)
            return None

    @staticmethod
    def run_simsummary(pnl_file: Path, config: Config) -> Metrics | None:
        try:
            result = subprocess.run(
                [config.python_path, config.simsummary_script, str(pnl_file),],
                capture_output=True,
                text=True,
                timeout=config.timeout
            )
            if result.returncode != 0:
                logger.warning("simsummary rc={} stderr={!r}", result.returncode, result.stderr[:500])
                return None

            lines = result.stdout.strip().split('\n')
            if not lines:
                return None

            # Normalize "shrp( ir)" → "shrp(ir)" so split() yields stable column positions
            # regardless of sign (positive IR has leading space inside parens, negative doesn't).
            last = re.sub(r'\(\s+', '(', lines[-1].strip())
            parts = last.split()
            if len(parts) < 11:
                return None

            try:
                ret = float(parts[4])
                tvr = float(parts[5])
                shrp_raw = parts[6]
                shrp = float(shrp_raw.split('(')[0].strip())
                mdd = float(parts[7])
                fitness = float(parts[9])
                return Metrics(ret, tvr, shrp, mdd, fitness)

            except (ValueError, IndexError) as e:
                logger.warning("simsummary parse failed: {} last_line={!r}", e, lines[-1] if lines else "")
                return None

        except Exception:
            logger.exception("run_simsummary failed pnl={}", pnl_file)
            return None

    @staticmethod
    def run_backtest(xml_file: Path, config: Config):
        result = subprocess.run(
            [config.python_path, config.run_script, xml_file],
            capture_output=True,
            text=True,
            timeout=config.timeout
        )

        if result.returncode != 0:
            raise BacktestError(result.stderr)

    @staticmethod
    def run_script(script: Path, args: list[str], python: str, cwd: Path,
                   config: Config, timeout: int | None = None) -> str:
        """跑 qr 自带脚本 (combo 的 predict.py / train.py)。

        用 `python` 指定的解释器 (combo 场景固定 gsim venv, 有 torch/lgbm),
        区别于 run_backtest 用 config.python_path (ops/gsim 工具的解释器)。
        失败抛 ScriptError, 成功返回 stdout。
        """
        result = subprocess.run(
            [python, str(script), *args],
            capture_output=True,
            text=True,
            timeout=timeout or config.timeout,
            cwd=str(cwd),
        )
        if result.returncode != 0:
            raise ScriptError(str(script), result.stderr)
        return result.stdout
