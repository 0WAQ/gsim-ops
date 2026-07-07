
import numpy as np

from ..metadata import AlphaMetadata
from .base import Result, Results, Status


class CompStatus(Status):
    PASS = 1
    FAIL = 2
    SKIP = 3

class CompResult(Result):
    def __init__(self, avg_long_pct: np.float64, avg_short_pct: np.float64,
                 long_count: int, short_count: int,
                 total_checked: int = 0):
        self.avg_long_pct = avg_long_pct
        self.avg_short_pct = avg_short_pct
        self.long_count = long_count
        self.short_count = short_count
        self.total_checked = total_checked


class CompResults(Results):
    def __init__(self):
        self.results: dict[AlphaMetadata, CompResult | str] = {}

    def get(self, key: AlphaMetadata, default: CompResult | str | None):
        return self.results.get(key, default)

    def __len__(self):
        return len(self.results)

    def __iter__(self):
        for k, v in self.results.items():
            yield k, v

    def __setitem__(self, key: AlphaMetadata, value: CompResult | str):
        self.results[key] = value

    def __getitem__(self, key: AlphaMetadata):
        return self.results[key]

    def __getattr__(self, name: str):
        return getattr(self.results, name)




