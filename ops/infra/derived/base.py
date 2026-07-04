"""派生层存储抽象.

因子库的"派生数据"—— 可从 JFS 一等数据 (源码/PNL/feature) 重建的元数据:
index (author/has_pnl/dump_days/delay) / metrics (simsummary) /
datasources (AST 解析 getData) / bcorr (相关性)。

历史上这些散在 per-machine JSON 缓存 (~/.cache/ops/lib/<lib>/*.json),三机各扫
各的、既慢又不一致。DerivedStore 把它们收拢到一个可查询、可共享的后端 (json 回退 /
postgres 生产),让 `ops list` 查询不扫盘、跨机一致。

设计要点:
- 键一律 (library_id, name);一个因子一行 (postgres 后端是 factor_derived 宽表)。
- 四组 (index/metrics/datasources/bcorr) 各自独立 upsert,互不覆盖 —— check 只更 metrics
  时不该抹掉 datasources。
- 读入口 get_all(),返回 {name: DerivedRecord};上层 (list.py) 内存 merge/filter。
  pg 后端另有 join_state()(LEFT JOIN factor_state),供 ops/infra/query.py 的联合读用。
- 健壮性铁律: 整表可从 JFS rebuild,后端丢了不致命。
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class DerivedRecord:
    """一个因子的全部派生数据 (四组合一)。字段可空 —— 未算过的组为 None。"""
    name: str

    # index 组
    author: str | None = None
    has_pnl: bool | None = None
    dump_days: int | None = None
    delay: int | None = None

    # metrics 组 (simsummary)
    ret: float | None = None
    shrp: float | None = None
    mdd: float | None = None
    tvr: float | None = None
    fitness: float | None = None

    # datasources 组
    fields: list[str] | None = None
    tables: list[str] | None = None

    # bcorr 组
    max_bcorr: float | None = None
    max_bcorr_factor: str | None = None


# 可过滤/排序的数值键 -> 从 DerivedRecord 取值的 Python 真相源。
# metric_get / sort_key 是内存侧唯一实现,list.py (过滤/兜底排序) 与
# json_store (回退后端下推) 都复用,pg_store 的 SQL 表达式 (_METRIC_EXPR)
# 必须逐键镜像这里的语义,三处不能 drift。
_SORTABLE_KEYS = ("ret", "shrp", "mdd", "tvr", "fitness", "dump_days", "delay", "bcorr")


def metric_get(rec: "DerivedRecord", key: str) -> float | None:
    """取 rec 的数值键用于阈值比较。None 表示该组未算过 (比较里应排除该行)。
    bcorr 用 abs(max_bcorr);dump_days/delay 转 float。"""
    if key == "bcorr":
        return abs(rec.max_bcorr) if rec.max_bcorr is not None else None
    if key == "dump_days":
        return float(rec.dump_days) if rec.dump_days is not None else None
    if key == "delay":
        return float(rec.delay) if rec.delay is not None else None
    v = getattr(rec, key, None)
    return float(v) if v is not None else None


def sort_key(rec: "DerivedRecord", key: str) -> float:
    """降序排序键。None 语义:dump_days None->0 (与旧 `dump_days or 0` 一致),
    其余 None->-inf (排最后)。bcorr 用 abs。"""
    if key == "dump_days":
        return float(rec.dump_days or 0)
    v = metric_get(rec, key)
    return v if v is not None else float("-inf")


class DerivedStore(ABC):
    """派生数据后端契约。四组各自 upsert,一个 get_all 读全库。"""

    @abstractmethod
    def get_all(
        self,
        author: str | None = None,
        *,
        field: str | None = None,
        table_glob: str | None = None,
        has_index: bool = False,
        metrics: list[tuple[str, str, float]] | None = None,
        sort_by: str | None = None,
        limit: int | None = None,
    ) -> dict[str, DerivedRecord]:
        """读全库派生数据,返回 {name: DerivedRecord}。author 给定则只返回该作者。

        field / table_glob 是可选的 datasource 反查下推:
          - field: 只返回 fields 数组含此值 (精确匹配) 的因子;
          - table_glob: 只返回 tables 数组任一元素 fnmatch 匹配此 glob 的因子。
        二者都为 None 时行为与无参一致。下推只做预筛缩小行集,上层仍会用
        apply_filters 全量兜底,故结果与不下推逐位等价 (下推纯为性能)。

        其余下推参数 (list.py 的排序/过滤/截断),同样是预筛,上层保留全量兜底:
          - has_index: True 只返回 author 非空的行 (即有 index 组 / 在 alpha_src)。
          - metrics: [(key, op, threshold)],key ∈ _SORTABLE_KEYS,op ∈ > >= < <= =
            (!= 不下推,调用方剔除)。语义与 metric_get + 比较一致 (None 行排除)。
          - sort_by: _SORTABLE_KEYS 之一,降序 (语义同 sort_key,None 排最后,name 二级序)。
          - limit: 只在结果集 == 最终结果集时由调用方传入 (gate 见 ops/infra/query.py),否则 None。
        """


    @abstractmethod
    def get(self, name: str) -> DerivedRecord | None:
        """读单个因子的派生数据。不存在返回 None。"""

    @abstractmethod
    def upsert_index(self, entries: dict[str, dict[str, Any]]) -> None:
        """批量写 index 组。entries: {name: {author, has_pnl, dump_days, delay}}。"""

    @abstractmethod
    def upsert_metrics(self, name: str, m: dict[str, Any]) -> None:
        """写单个因子的 metrics 组。m: {ret, shrp, mdd, tvr, fitness}。"""

    @abstractmethod
    def upsert_datasources(self, name: str, fields: list[str], tables: list[str]) -> None:
        """写单个因子的 datasources 组。"""

    @abstractmethod
    def upsert_bcorr(self, name: str, max_bcorr: float, max_bcorr_factor: str) -> None:
        """写单个因子的 bcorr 组。"""

    @abstractmethod
    def delete(self, name: str) -> bool:
        """删一个因子的派生数据。返回是否存在过。"""

    @abstractmethod
    def get_meta(self, key: str) -> str | None:
        """读一个 library 级元数据值 (如 index_built_at)。不存在返回 None。"""

    @abstractmethod
    def set_meta(self, key: str, value: str) -> None:
        """写一个 library 级元数据值。"""
