from abc import ABC, abstractmethod

from ops.core.state import CheckRecord, FactorRecord, FactorStatus


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
    def transition(self, name: str, to_status: FactorStatus, **updates) -> FactorRecord: ...

    @abstractmethod
    def append_check(self, name: str, check: CheckRecord) -> None: ...

    @abstractmethod
    def delete(self, name: str) -> bool:
        """Remove a record. Returns True if it existed."""
