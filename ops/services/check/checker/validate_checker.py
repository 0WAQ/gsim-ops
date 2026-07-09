from ops.core.alpha.metadata import AlphaMetadata
from ops.infra.config import Config
from ops.infra.gsim.runner import BacktestError, Runner

from .base import Checker, CheckFail


class ValidateChecker(Checker):
    """Short backtest without firewall — validates factor code/config can run."""

    def __init__(self, config: Config):
        self.config = config

    def check(self, factor: AlphaMetadata):
        try:
            Runner.run_backtest(factor.xml_file, self.config)
        except BacktestError as e:
            raise CheckFail(e)
