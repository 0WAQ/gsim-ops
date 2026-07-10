"""Factor 聚合 —— 全库唯一叫"因子"的领域类型(factor-aggregate-plan §3.1)。

一个因子 = 三个切面:
  - identity(FactorIdentity):身份,不可变 —— name/author/discovery_method/created_at,
    落 PG factor_info 表(三表之根,FK 级联);
  - state(FactorRecord,见 core/state.py):生命周期状态机;
  - snapshot(FactorSnapshot):入库时不可变快照(未入库 = None)。

service 层只见 `Factor`(由 FactorRepository 组装);三张表各自的 dataclass 降级
为 Repository/store 的内部行网关。此前领域模型碎成 12 个按存储介质命名的投影、
零个类型代表"因子"本身(full-review 第三部分),本类型是那次诊断的答案。

**不变量(构造时软校验,坏数据 warn 不炸)**:snapshot 存在 ⇒
`snapshot.snapshot_at == state.entered_at`(快照语义 = "本次入库事件的快照")。
注意 *不是* "ACTIVE ⇒ snapshot 存在":`ops approve`(correlation 拒绝的多样性
豁免)合法产生无快照的 ACTIVE 因子(REJECTED 不写快照,approve 只翻状态)。
"""
from __future__ import annotations

from dataclasses import dataclass

from ops.core.state import FactorRecord, FactorStatus
from ops.utils.log import logger


@dataclass(frozen=True)
class FactorIdentity:
    """因子身份(不可变)。PG factor_info 表的领域形态。

    2026-07-09 自 infra/info/base.py 的 FactorInfo 正名迁入 core:聚合根的切面
    是领域概念,三表存储是 infra 细节。infra/info 以别名保住存量导入路径。
    """
    name: str
    author: str | None = None
    discovery_method: str | None = None  # 'automated' | 'manual'
    created_at: str | None = None  # ISO timestamp


@dataclass
class FactorSnapshot:
    """因子入库时快照(不可变)。PG factor_snapshot 表的领域形态。

    所有字段都是 check 通过时(factor_state.entered_at)的状态,之后永不更新;
    需要最新表现必须重跑 backtest(`ops refresh` 已删除,无重算路径)。
    2026-07-09 自 infra/snapshot/base.py 迁入 core(理由同 FactorIdentity)。
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

    # delay 入库时从 XML 解析定死,与 metrics 同性质不可变。has_pnl/dump_days
    # 曾在此,因是可变物理事实与快照语义冲突已删列;实时物理状态走 LibraryScanner。
    delay: int | None = None

    # bcorr 组 (入库时计算)
    max_bcorr: float | None = None
    max_bcorr_factor: str | None = None

    # 快照时间点 = factor_state.entered_at (入库时间)
    snapshot_at: str | None = None  # ISO timestamp


@dataclass(frozen=True)
class Factor:
    """一个因子(identity + state + snapshot 三切面的聚合)。

    - state 为 None:factor_info 有行但 factor_state 无(异常孤儿,对账场景);
    - snapshot 为 None:未入库,或经 approve 豁免入库(合法无快照)。
    frozen 只冻结切面绑定;切面对象本身的可变性由各自类型决定。
    """
    identity: FactorIdentity
    state: FactorRecord | None = None
    snapshot: FactorSnapshot | None = None

    def __post_init__(self) -> None:
        # 软校验:快照必须锚定当次入库事件。迁移期残留/删除失败的 stale 快照在
        # 这里现形(U2 鬼影);warn 不炸 —— 读路径不应因坏数据拒绝服务。
        if (self.snapshot is not None and self.state is not None
                and self.snapshot.snapshot_at != self.state.entered_at):
            logger.warning(
                "Factor {}: snapshot_at={} != entered_at={} (stale 快照,需对账)",
                self.identity.name, self.snapshot.snapshot_at, self.state.entered_at)

    @property
    def name(self) -> str:
        return self.identity.name

    @property
    def status(self) -> FactorStatus | None:
        """None = 无 state 记录(异常孤儿)。"""
        return self.state.status if self.state is not None else None

    @property
    def last_fail_stage(self) -> str | None:
        return self.state.last_fail_stage if self.state is not None else None
