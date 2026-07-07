from dataclasses import dataclass, field, asdict
from enum import Enum


class FactorStatus(str, Enum):
    SUBMITTED = "submitted"
    CHECKING  = "checking"
    ACTIVE    = "active"
    REJECTED  = "rejected"
    DECAYING  = "decaying"
    RETIRED   = "retired"


@dataclass
class CheckRecord:
    started_at: str
    finished_at: str | None = None
    passed: bool | None = None
    failed_stage: str | None = None
    fail_reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CheckRecord":
        return cls(**d)


@dataclass
class FactorRecord:
    """因子生命周期状态（纯状态机，不含身份信息）。

    author 已移到 FactorInfo，discovery_method 同理。
    本 record 只管状态转移: SUBMITTED -> CHECKING -> ACTIVE/REJECTED。
    """
    name: str
    status: FactorStatus
    updated_at: str
    submitted_at: str | None = None
    entered_at: str | None = None
    rejected_at: str | None = None
    last_fail_stage: str | None = None
    last_fail_reason: str | None = None
    version: int = 1
    check_history: list[CheckRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "FactorRecord":
        d = dict(d)
        d["status"] = FactorStatus(d["status"])
        d.setdefault("version", 1)
        d["check_history"] = [CheckRecord.from_dict(c) for c in d.get("check_history", [])]
        return cls(**d)
