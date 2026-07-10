"""factor_info 表的 store 抽象(数据形态已正名迁 core)。

数据类 2026-07-09 迁 `ops/core/factor.py::FactorIdentity`(Factor 聚合的身份
切面;core 不能 import infra,聚合切面必须住 core)。`FactorInfo` 别名保住
存量导入路径 —— store 层内部仍以"行网关"角色使用它,service 新代码应从
core 取 FactorIdentity(经 FactorRepository,不直接碰本层)。
"""
from abc import ABC, abstractmethod

from ops.core.factor import FactorIdentity

FactorInfo = FactorIdentity


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
