from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ops.core.factor import FactorSnapshot


@dataclass
class Metrics:
    ret: float
    tvr: float
    shrp: float
    mdd: float
    fitness: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "ret%": self.ret,
            "tvr%": self.tvr,
            "shrp": self.shrp,
            "mdd%": self.mdd,
            "fitness": self.fitness,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Metrics":
        return cls(
            ret=data["ret%"],
            tvr=data["tvr%"],
            shrp=data["shrp"],
            mdd=data["mdd%"],
            fitness=data["fitness"],
        )

    def __repr__(self):
        return f"ret={self.ret}%, shrp={self.shrp}, mdd={self.mdd}%, tvr={self.tvr}%, fitness={self.fitness}"

    def __str__(self):
        return self.__repr__()


@dataclass(frozen=True)
class MetricSpec:
    """一个可过滤/排序的 snapshot metric 键的语义。

    column 同时是 FactorSnapshot 属性名和 factor_snapshot 列名(两边天然同名);
    absolute 表示取值语义带绝对值(bcorr:相关性只看幅度不看方向)。
    """
    column: str
    absolute: bool = False


# metric 事实族的唯一定义(SSOT S8):键集 + 每键的取值语义。
# 三个消费方全部由此派生,不再各抄一份:
#   - SQL 下推表达式:infra/snapshot/pg_store._prefixed_metric_expr
#   - 内存取值(过滤/排序兜底):下方 metric_value,services/list 用
#   - CLI --sort-by choices:cli/common.py 经 METRIC_SORT_KEYS re-export
# 新增可排序 metric = 在此加一行(snapshot 表须有对应列)。
SNAPSHOT_METRICS: dict[str, MetricSpec] = {
    "ret": MetricSpec("ret"),
    "shrp": MetricSpec("shrp"),
    "mdd": MetricSpec("mdd"),
    "tvr": MetricSpec("tvr"),
    "fitness": MetricSpec("fitness"),
    "bcorr": MetricSpec("max_bcorr", absolute=True),
}


def metric_value(snapshot: FactorSnapshot | None, key: str) -> float | None:
    """按注册表语义从 snapshot 取 metric 值(内存侧过滤/排序用)。
    未注册的键 / 无快照 / 值缺失均返回 None。"""
    spec = SNAPSHOT_METRICS.get(key)
    if spec is None or snapshot is None:
        return None
    v = getattr(snapshot, spec.column, None)
    if v is None:
        return None
    return abs(v) if spec.absolute else v
