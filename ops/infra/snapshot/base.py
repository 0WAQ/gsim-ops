"""factor_snapshot 表的 store 抽象(数据形态已迁 core)。

数据类已迁 `ops/core/factor.py::FactorSnapshot`(Factor 聚合的快照
切面;core 不能 import infra,聚合切面必须住 core)。此处 re-import 保住
`from ops.infra.snapshot import FactorSnapshot` 的存量路径。语义不变:入库时
不可变快照,只有 insert(check 通过时)和 delete(离库时),没有 update。
"""
from abc import ABC, abstractmethod

from ops.core.factor import FactorSnapshot

__all__ = ["FactorSnapshot", "SnapshotStore"]


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
    def delete(self, name: str) -> bool:
        """删除因子快照(rm / restage / overwrite 离库时)。

        返回 True 表示行存在且已删除(与 StateStore/InfoStore 的 delete 契约
        对齐 —— 同一动词一种返回约定)。"""

    @abstractmethod
    def list(
        self,
        *,
        field: str | None = None,
        table_glob: str | None = None,
        metrics: list[tuple[str, str, float]] | None = None,
        sort_by: str | None = None,
        limit: int | None = None,
    ) -> dict[str, FactorSnapshot]:
        """列出所有因子快照，支持过滤/排序/截断。

        参数与原 DerivedStore.get_all 一致，用于 ops list 查询。
        """
