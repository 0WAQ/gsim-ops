from ops.core.alpha.metadata import AlphaMetadata
from ops.infra.config import Config
from ops.infra.gsim.runner import BacktestError, Runner

from .base import Checker, CheckFail


class LongBacktestChecker(Checker):
    """Full-history backtest (LONG_BACKTEST_WINDOW, 见 xml_prepare) — pure run, no checks."""

    def __init__(self, config: Config):
        self.config = config

    def check(self, factor: AlphaMetadata):
        try:
            Runner.run_backtest(factor.xml_file, self.config)
        except BacktestError as e:
            raise CheckFail(e)
