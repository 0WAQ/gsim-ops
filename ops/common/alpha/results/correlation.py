from enum import Enum
from typing import Dict
from ...metrics import Metrics
from ..metadata import AlphaMetadata

class CorrStatus(Enum):
    PASS = 1
    BEAT = 2
    FAIL = 3
    ERROR = 4

class CorrResult:
    def __init__(self, max_bcorr: float, max_bcorr_factor: str,
            metrics: Metrics, high_corr_count: int,
            unbeaten_example: tuple[str, float, Metrics] | None = None):
        self.max_bcorr = max_bcorr
        self.max_bcorr_factor = max_bcorr_factor
        self.metrics = metrics
        self.high_corr_count = high_corr_count
        self.unbeaten_example = unbeaten_example

class CorrResults:
    def __init__(self):
        self.results: Dict[AlphaMetadata, CorrResult | str] = {}

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

