from abc import ABC, abstractmethod

from ops.core.state import CheckRecord, FactorRecord, FactorStatus, HistoryEvent

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
                   expect: FactorStatus | None = None,
                   op: str | None = None, actor: str | None = None,
                   **updates) -> FactorRecord:
        """状态转移。expect 非 None 时做 CAS:当前 status != expect 抛
        StateConflict(原实现无任何 from-status 守卫,任何状态可被翻成任何
        状态,full-review 第三部分)。

        op 非 None 时同事务发射 factor_history 事件(schema v2b);此外
        to_status == ACTIVE 时**无条件**自动发射 'entered'(入库事实的统一
        标记,check 归档 / approve / backfill 三径合流,漏记结构上不可能)。
        json dev/test 后端无事件表,op/actor 接受并忽略。"""
        ...

    @abstractmethod
    def append_check(self, name: str, check: CheckRecord,
                     actor: str | None = None) -> None:
        """记一次 check 完成(pass/fail/skip)。PG 后端 = factor_history 插
        op='check' 事件行 + 触 updated_at;json 后端 = 记录内 check_history
        追加(dev/test 形态)。"""
        ...

    @abstractmethod
    def delete(self, name: str, op: str | None = None,
               actor: str | None = None) -> bool:
        """Remove a record. Returns True if it existed.
        op 非 None 时(rm/cancel)同事务发射事件 —— 事件无 FK,活过删除。"""
        ...

    @abstractmethod
    def checks(self, name: str) -> "list[CheckRecord]":
        """check 全史(v2c:自 FactorRecord 剥离,按需查)。PG 后端从事件表
        组装;json 后端读记录侧存的原始列表。"""
        ...

    @abstractmethod
    def last_fail(self, name: str) -> HistoryEvent | None:
        """最近一次 check 失败(op='check' AND passed=FALSE 的最新事件)。
        原 factor_state 三列 rejected_at/last_fail_* 的派生替身(v2b)。
        json 后端从 check_history 内存扫描合成。None = 从未失败。"""
        ...

    @abstractmethod
    def latest_check_ats(self) -> "dict[str, str]":
        """全库 name → 最近一次 check 事件的 at(schema v3:doctor 用它对账
        测得快照 —— snapshot_at 应等于最近一次测得的 check 时刻)。
        json dev/test 后端从记录侧 check 列表合成。"""
        ...

    @abstractmethod
    def history(self, name: str) -> "list[HistoryEvent]":
        # 注解加引号:类体内裸 list 会被上面的同名抽象方法遮蔽(TypeError)
        """完整生命周期事件时间线(at 升序)。json dev/test 后端无事件表,
        返回 [](cli 回落到 check_history 渲染)。"""
        ...
