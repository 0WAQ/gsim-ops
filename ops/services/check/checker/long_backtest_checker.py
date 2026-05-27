from .base import *
from ops.infra.config import Config
from ops.infra.gsim.runner import Runner, BacktestError
from ops.core.alpha.metadata import AlphaMetadata


class LongBacktestFail(CheckFail):
    def __init__(self, *args: object):
        super().__init__("long_backtest", *args)


class LongBacktestSkip(CheckSkip):
    def __init__(self, *args: object):
        super().__init__("long_backtest", *args)


class LongBacktestChecker(Checker):
    """Full-history backtest (20150101-20251231) — pure run, no checks."""

    def __init__(self, config: Config):
        self.config = config

    def check(self, factor: AlphaMetadata):
        try:
            Runner.run_backtest(factor.xml_file, self.config)
        except BacktestError as e:
            raise LongBacktestFail(e)
