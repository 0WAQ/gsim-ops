from abc import ABC, abstractmethod

from ops.core.state import FactorRecord, FactorStatus, CheckRecord


class StateStore(ABC):
    @abstractmethod
    def get(self, name: str) -> FactorRecord | None: ...

    @abstractmethod
    def put(self, record: FactorRecord) -> None: ...

    @abstractmethod
    def list(self,
             author: str | None = None,
             status: FactorStatus | None = None) -> list[FactorRecord]: ...

    @abstractmethod
    def transition(self, name: str, to_status: FactorStatus, **updates) -> FactorRecord: ...

    @abstractmethod
    def append_check(self, name: str, check: CheckRecord) -> None: ...
