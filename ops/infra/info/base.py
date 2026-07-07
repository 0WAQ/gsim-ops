"""Factor 身份信息数据模型与 store 抽象."""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class FactorInfo:
    """因子身份信息（不可变）。

    从 factor_state.author 移入，与生命周期状态分离。
    discovery_method 用于 bcorr 池的选择 (automated/manual)。
    """
    name: str
    author: str | None = None
    discovery_method: str | None = None  # 'automated' | 'manual'
    created_at: str | None = None  # ISO timestamp


class InfoStore(ABC):
    """factor_info 表的抽象接口。

    与 factor_state (状态) / factor_snapshot (快照) 分离，只管身份信息。
    """

    @abstractmethod
    def get(self, name: str) -> FactorInfo | None:
        """读取单个因子的身份信息。"""

    @abstractmethod
    def upsert(self, info: FactorInfo) -> None:
        """插入或更新因子身份信息。

        submit 时插入，后续一般不改（author/discovery_method 是不可变属性）。
        """

    @abstractmethod
    def delete(self, name: str) -> bool:
        """删除因子身份信息（级联删除 state/snapshot）。

        返回 True 表示行存在且已删除(与 StateStore.delete 契约对齐;原返回 None
        导致 rm 的 `if delete(name):` 确认信息永不打印,full-review 第三部分 D3)。
        """

    @abstractmethod
    def list(self, author: str | None = None) -> list[FactorInfo]:
        """列出所有因子的身份信息，可按 author 过滤。"""
