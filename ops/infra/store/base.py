from abc import ABC, abstractmethod

from ops.core.state import CheckRecord, FactorRecord, FactorStatus


class StateConflict(RuntimeError):
    """transition(expect=...) 的 CAS 失败:当前 status 与期望不符。

    TOCTOU 修复的一半 (full-review 第三部分 §3.2):resolve 与执行之间状态
    可能被并发操作改变,transition 提供 from-status 条件更新,调用方捕获后
    按'跳过'处理而不是盲改。
    """


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
