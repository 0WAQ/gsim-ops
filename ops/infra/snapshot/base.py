"""Factor 入库时快照数据模型与 store 抽象."""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class FactorSnapshot:
    """因子入库时快照（不可变）。

    所有字段都是 check 通过时（factor_state.entered_at）的状态，之后永不更新。

    语义变更（2026-07-06）:
    - 之前: ret/shrp/datasources 等反映"最新表现"（可 ops refresh 重算）
    - 之后: 所有字段都是"入库时表现"（snapshot_at 那一刻的快照），永不可变

    **重要**: ret/shrp/mdd/tvr/fitness 等指标的含义改变:
    - 旧语义: 最新的 backtest 结果（今天重跑可能得到不同的值）
    - 新语义: 因子入库时（通过 check）的 backtest 结果（固定不变）

    这是一个**语义破坏性变更** —— 现有代码读取这些字段时,必须理解它们代表"入库时表现",
    而非"当前最新表现"。如果需要最新表现,必须重跑 backtest。
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

    # index 组 (入库时扫盘)
    has_pnl: bool | None = None
    dump_days: int | None = None
    delay: int | None = None

    # bcorr 组 (入库时计算)
    max_bcorr: float | None = None
    max_bcorr_factor: str | None = None

    # 快照时间点 = factor_state.entered_at (入库时间)
    snapshot_at: str | None = None  # ISO timestamp


class SnapshotStore(ABC):
    """factor_snapshot 表的抽象接口。

    快照是不可变的：只有 insert（check 通过时）和 delete（ops rm），没有 update。
    """

    @abstractmethod
    def get(self, name: str) -> FactorSnapshot | None:
        """读取单个因子的入库时快照。"""

    @abstractmethod
    def insert(self, snapshot: FactorSnapshot) -> None:
        """插入入库时快照（check 通过时一次性写入）。

        如果已存在则报错（不应重复入库同一因子）。
        """

    @abstractmethod
    def delete(self, name: str) -> None:
        """删除因子快照（ops rm 时）。"""

    @abstractmethod
    def list(
        self,
        *,
        field: str | None = None,
        table_glob: str | None = None,
        has_index: bool = False,
        metrics: list[tuple[str, str, float]] | None = None,
        sort_by: str | None = None,
        limit: int | None = None,
    ) -> dict[str, FactorSnapshot]:
        """列出所有因子快照，支持过滤/排序/截断。

        参数与原 DerivedStore.get_all 一致，用于 ops list 查询。
        """
