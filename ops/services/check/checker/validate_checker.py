from .base import *
from ops.infra.config import Config
from ops.infra.gsim.runner import Runner, BacktestError
from ops.core.alpha.metadata import AlphaMetadata


class ValidateFail(CheckFail):
    def __init__(self, *args: object):
        super().__init__("validate", *args)


class ValidateSkip(CheckSkip):
    def __init__(self, *args: object):
        super().__init__("validate", *args)


class ValidateChecker(Checker):
    """Short backtest without firewall — validates factor code/config can run."""

    def __init__(self, config: Config):
        self.config = config

    def check(self, factor: AlphaMetadata):
        try:
            Runner.run_backtest(factor.xml_file, self.config)
        except BacktestError as e:
            raise ValidateFail(e)
