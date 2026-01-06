from dataclasses import dataclass
from .key import AlphaKey
from .results.compliance import CompResult
from .results.correlation import CorrResult
from .results.checkpoint import PointResult

@dataclass
class AlphaReport:
    key: AlphaKey
    compliance_result: CompResult | None = None
    correlation_result: CorrResult | None = None
    checkpoint_result: PointResult | None = None

