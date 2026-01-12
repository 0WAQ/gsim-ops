import re
import subprocess
from pathlib import Path

from .config import Config
from .metrics import Metrics


class BacktestError(Exception):
    def __init__(self, *args: object):
        self.stage = "backtest"
        super().__init__(*args)

    def __repr__(self):
        if len(self.args) == 0:
            return ""
        if len(self.args) > 1:
            return repr(self.args[0])
        return repr(self.args)


class Runner:
    @staticmethod
    def run_bcorr(pnl_file: Path, config: Config) -> list[tuple[str, float]] | None:
        try:
            result = subprocess.run(
                [config.bcorr_script, str(pnl_file), str(config.pnl_prod_path)],
                capture_output=True,
                text=True,
                timeout=config.timeout
            )
            if result.returncode != 0:
                return None

            corrs: list[tuple[str, float]] = []
            for line in result.stdout.strip().split('\n'):
                match = re.match(r"^(\S+)\s+([-\d.]+)", line.strip())
                if match:
                    corrs.append((match.group(1), float(match.group(2))))
            return corrs
    
        except Exception:
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
                return None
            
            lines = result.stdout.strip().split('\n')
            if not lines:
                return None
    
            parts = lines[-1].strip().split()
            if len(parts) < 11:
                return None
        
            try:
                ret = float(parts[4])
                shrp_raw = parts[6]
                shrp = float(shrp_raw.split('(')[0].strip())
                fitness = float(parts[9])
                return Metrics(ret, shrp, fitness)
                
            except (ValueError, IndexError) as e:
                return None
                
        except Exception as e:
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
