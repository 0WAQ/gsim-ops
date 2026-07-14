"""Factor 聚合 —— 全库唯一叫"因子"的领域类型。

一个因子 = 三个切面:
  - identity(FactorIdentity):身份,不可变 —— name/author/discovery_method/created_at,
    落 PG factor_info 表(三表之根,FK 级联);
  - state(FactorRecord,见 core/state.py):生命周期状态机;
  - snapshot(FactorSnapshot):测得快照(最近一次 check 测得的表现;未测得 = None)。

service 层只见 `Factor`(由 FactorRepository 组装);三张表各自的 dataclass 降级
为 Repository/store 的内部行网关。

**不变量(doctor 全权对账,构造零校验)**:snapshot 存在 ⇒
`snapshot_at == 最近一次 check 事件的 at`(测得快照语义;legacy 无事件
锚 entered_at)。approve 放行的因子带着被拒那次的测得快照转 ACTIVE。
"""
from __future__ import annotations

from dataclasses import dataclass

from ops.core.state import CORRELATION, FactorRecord, FactorStatus, HistoryEvent


@dataclass(frozen=True)
class FactorIdentity:
    """因子身份(不可变)。PG factor_info 表的领域形态。

    聚合根的切面是领域概念,三表存储是 infra 细节:dataclass 正主在此,
    infra/info 以别名保住存量导入路径。
    """
    name: str
    author: str | None = None
    discovery_method: str | None = None  # 'automated' | 'manual'
    created_at: str | None = None  # ISO timestamp


@dataclass
class FactorSnapshot:
    """**测得快照**:最近一次 check 测得的表现。

    PG factor_snapshot 表的领域形态。pass 与 correlation/compliance 失败都写
    (被拒因子也有测得值);snapshot_at = 测得时刻(该次 check 事件的 at)。
    每行不可变,新测量原子替换;仍然只由 check 写,永无离线重算
    (`ops refresh` 已删除)。写快照 ≠ 入库 —— 入库判据看 status/entered_at。
    """
    name: str

    # metrics 组 (入库时 backtest 结果)
    ret: float | None = None
    shrp: float | None = None
    mdd: float | None = None
    tvr: float | None = None
    fitness: float | None = None

    # datasources 组 (入库时代码解析)
    fields: list[str] | None = None
    tables: list[str] | None = None

    # delay 入库时从 XML 解析定死,与 metrics 同性质不可变。别把 has_pnl/dump_days
    # 加回来:可变物理事实与快照语义冲突;实时物理状态走 LibraryScanner。
    delay: int | None = None

    # bcorr 组 (入库时计算)
    max_bcorr: float | None = None
    max_bcorr_factor: str | None = None

    # 测得时刻 = 该次 check 事件的 at(pass 因子恰与 entered_at 同值)
    snapshot_at: str | None = None  # ISO timestamp


@dataclass(frozen=True)
class Factor:
    """一个因子(identity + state + snapshot 三切面的聚合)。

    - state 为 None:factor_info 有行但 factor_state 无(异常孤儿,对账场景);
    - snapshot 为 None:从未测得(早期 stage 失败 / 从未 check 的 legacy)。
    - last_fail:最近一次 check 失败(factor_history 的派生事实;state 不再存
      rejected_at/last_fail_* 列)。None = 从未失败/无事件。
      注意它不随 approve/restage 清空 —— 消费方一律与 status 联判。
    frozen 只冻结切面绑定;切面对象本身的可变性由各自类型决定。
    """
    identity: FactorIdentity
    state: FactorRecord | None = None
    snapshot: FactorSnapshot | None = None
    last_fail: HistoryEvent | None = None

    @property
    def name(self) -> str:
        return self.identity.name

    @property
    def status(self) -> FactorStatus | None:
        """None = 无 state 记录(异常孤儿)。"""
        return self.state.status if self.state is not None else None

    @property
    def last_fail_stage(self) -> str | None:
        return self.last_fail.failed_stage if self.last_fail is not None else None

    @property
    def last_fail_reason(self) -> str | None:
        return self.last_fail.fail_reason if self.last_fail is not None else None

    def correlation_rejected(self) -> bool:
        """approve(多样性豁免)的资格谓词:当前 REJECTED 且最近失败在
        correlation stage。其他阶段(checkbias/checkpoint/compliance)是
        质量问题,不属豁免范畴。(谓词落在聚合而非 state:需要 state + history
        两个切面。)"""
        return (self.state is not None
                and self.state.status == FactorStatus.REJECTED
                and self.last_fail is not None
                and self.last_fail.failed_stage == CORRELATION)
