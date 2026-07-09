
from ...metrics import Metrics
from ..metadata import AlphaMetadata
from .base import Result, Results, Status


class CorrStatus(Status):
    PASS = 1
    BEAT = 2
    FAIL = 3
    ERROR = 4

class CorrResult(Result):
    def __init__(self,
                metrics: Metrics, 
                max_bcorr: float | None = None,
                max_bcorr_factor: str | None = None,
                high_corr_count: int | None = None,
                unbeaten_example: tuple[str, float, Metrics] | None = None):
        self.metrics = metrics
        self.max_bcorr = max_bcorr
        self.max_bcorr_factor = max_bcorr_factor
        self.high_corr_count = high_corr_count
        self.unbeaten_example = unbeaten_example

    def __repr__(self):
        if self.max_bcorr is None:
            return f"{self.metrics}"
        return f"bcorr={self.max_bcorr}, {self.metrics}"
    
    def __str__(self):
        if self.max_bcorr is None:
            return f"{self.metrics}"
        return f"bcorr={self.max_bcorr}, {self.metrics}"

class CorrResults(Results):
    def __init__(self):
        self.results: dict[AlphaMetadata, CorrResult | str] = {}

    def get(self, key: AlphaMetadata, default: CorrResult | str | None):
        return self.results.get(key, default)

    def __len__(self):
        return len(self.results)

    def __iter__(self):
        for k, v in self.results.items():
            yield k, v

    def __setitem__(self, key: AlphaMetadata, value: CorrResult | str):
        self.results[key] = value

    def __getitem__(self, key: AlphaMetadata):
        return self.results[key]

    def __getattr__(self, name: str):
        return getattr(self.results, name)

