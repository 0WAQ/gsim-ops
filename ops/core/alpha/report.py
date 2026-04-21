from .key import AlphaKey
from .results.base import Result
from .results.compliance import CompResult
from .results.correlation import CorrResult
from .results.checkpoint import PointResult

class AlphaReport:
    def __init__(self, key: AlphaKey):
        self.key = key
        self.results: list[Result | None] = []

    def append(self, report: Result | None):
        self.results.append(report)

