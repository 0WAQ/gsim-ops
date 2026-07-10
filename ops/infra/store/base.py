from abc import ABC, abstractmethod

from ops.core.state import CheckRecord, FactorRecord, FactorStatus

# 定义已迁 ops/infra/errors.py(D3 类型化异常集中地);此处 re-export 保住
# `from ops.infra.store import StateConflict` 的存量导入路径。
from ops.infra.errors import StateConflict

__all__ = ["StateConflict", "StateStore"]


class StateStore(ABC):
    @abstractmethod
    def get(self, name: str) -> FactorRecord | None: ...

    @abstractmethod
    def put(self, record: FactorRecord) -> None: ...

    # author 参数已删:2026-07-06 三表拆分后 FactorRecord 不再有 author,PG 实现
    # 早已只收 status(ABC 与实现签名漂移 = LSP 违反,full-review 第一部分 P1 表)。
    # author 过滤走 InfoStore.list(author=...)。
    @abstractmethod
    def list(self, status: FactorStatus | None = None) -> list[FactorRecord]: ...

    @abstractmethod
    def transition(self, name: str, to_status: FactorStatus,
                   expect: FactorStatus | None = None, **updates) -> FactorRecord:
        """状态转移。expect 非 None 时做 CAS:当前 status != expect 抛
        StateConflict(原实现无任何 from-status 守卫,任何状态可被翻成任何
        状态,full-review 第三部分)。"""
        ...

    @abstractmethod
    def append_check(self, name: str, check: CheckRecord) -> None: ...

    @abstractmethod
    def delete(self, name: str) -> bool:
        """Remove a record. Returns True if it existed."""
