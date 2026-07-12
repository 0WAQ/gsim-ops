"""list 查询编排 —— 零展示(2026-07-11 展示层上收:表格/JSON 渲染在
`ops/cli/list.py`,本模块只负责解析过滤条件 + 下推查询 + 内存兜底,返回
`list[Factor]`;解析失败抛 `FilterError`,由 cli 呈现)。"""
import fnmatch
import re

from ops.core.factor import Factor
from ops.core.metrics import SNAPSHOT_METRICS, metric_value
from ops.infra.config import Config
from ops.infra.repository import FactorRepository

_FILTER_PATTERN = re.compile(r"^(\w+)([><=!]+)(.+)$")
# 可排序/过滤的 metric 键 —— 从注册表派生(SSOT S8,core/metrics.py),
# 取值语义(bcorr=abs)也在注册表,本文件不再自带镜像。
_SORTABLE_KEYS = frozenset(SNAPSHOT_METRICS)
FILTER_KEYS = {"tables", "field"} | _SORTABLE_KEYS
# 合法比较符白名单:typo(=>、=<、>< 等)能通过正则但下推白名单和内存 if 链都
# 没有分支 —— 旧行为是**静默吞掉该条件**且新旧路径因子集还不一致(对抗评审),
# 一律在解析期响亮拒绝。
_VALID_OPS = {">", ">=", "<", "<=", "=", "!="}


class FilterError(ValueError):
    """`--filter-by` 表达式非法。`errors` 是给用户看的逐条纯文本信息
    (渲染归 cli,这里不带任何标记语法)。"""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def parse_filters(filter_str: str) -> list[tuple[str, str, str]]:
    """解析 --filter-by 逗号分隔表达式;任一条非法即收集全部错误抛 FilterError。"""
    filters: list[tuple[str, str, str]] = []
    errors: list[str] = []
    for part in filter_str.split(","):
        part = part.strip()
        if not part:
            continue
        m = _FILTER_PATTERN.match(part)
        if m:
            key, op, value = m.group(1), m.group(2), m.group(3)
            if key not in FILTER_KEYS:
                errors.append(f"Unknown filter key: '{key}'. Supported: {', '.join(sorted(FILTER_KEYS))}")
                continue
            if op not in _VALID_OPS:
                hint = " (did you mean '>=')" if op == "=>" else \
                       " (did you mean '<=')" if op == "=<" else ""
                errors.append(f"Unknown operator: '{op}'{hint}. "
                              f"Supported: {', '.join(sorted(_VALID_OPS))}")
                continue
            filters.append((key, op, value))
        else:
            errors.append(f"Invalid filter syntax: '{part}'. Expected: key=value or key>value (use quotes: --filter-by \"...\")")
    if errors:
        raise FilterError(errors)
    return filters


def apply_filters(rows: list[Factor], filters: list[tuple[str, str, str]]) -> list[Factor]:
    """内存侧过滤（兜底，保证与下推结果逐位等价）。"""
    result = rows
    for key, op, value in filters:
        if key == "tables":
            result = [
                x for x in result
                if x.snapshot and x.snapshot.tables and any(fnmatch.fnmatch(t, value) for t in x.snapshot.tables)
            ]
        elif key == "field":
            result = [x for x in result if x.snapshot and x.snapshot.fields and value in x.snapshot.fields]
        elif key in _SORTABLE_KEYS:
            threshold = float(value)
            if op == ">":
                result = [x for x in result if (v := metric_value(x.snapshot, key)) is not None and v > threshold]
            elif op == ">=":
                result = [x for x in result if (v := metric_value(x.snapshot, key)) is not None and v >= threshold]
            elif op == "<":
                result = [x for x in result if (v := metric_value(x.snapshot, key)) is not None and v < threshold]
            elif op == "<=":
                result = [x for x in result if (v := metric_value(x.snapshot, key)) is not None and v <= threshold]
            elif op == "=":
                result = [x for x in result if (v := metric_value(x.snapshot, key)) is not None and v == threshold]
    return result


def _pushdown_params(filters: list[tuple[str, str, str]]) -> tuple[str | None, str | None]:
    """从已解析的 filters 里挑出第一个 field= 和第一个 tables= 条件的 value,
    作为 get_all 的 SQL 下推参数。多个同类条件时只下推第一个,其余留给
    apply_filters 内存兜底 —— 下推只做预筛,不改变最终结果。"""
    field = next((v for k, _, v in filters if k == "field"), None)
    table_glob = next((v for k, _, v in filters if k == "tables"), None)
    return field, table_glob


def _metric_pushdown(filters: list[tuple[str, str, str]]) -> list[tuple[str, str, float]]:
    """把 metric 阈值条件 (ret>30 等) 转成 get_all 的下推参数。
    `!=` 不下推 (apply_filters 未实现,现状静默忽略),剔除以保持逐位等价;
    apply_filters 仍全量兜底,故下推纯为预筛。"""
    out: list[tuple[str, str, float]] = []
    for key, op, value in filters:
        if key in _SORTABLE_KEYS and op != "!=":
            out.append((key, op, float(value)))
    return out


def list_factors(args) -> list[Factor]:
    """列出库内因子 —— 零扫盘,纯 PG catalog 查询;返回已过滤/排序/截断的行。

    `--filter-by` 非法时抛 `FilterError`(含逐条错误信息)。

    2026-07-07 Wave 2 (JOURNAL V1): 因子集判据收敛为
    `factor_state.status != 'submitted'`(在 repo.find 里定义,PG 是唯一
    权威)。原扫盘白名单 + derived 索引缓存路径删除 —— 缓存自三表迁移起已坏,
    每次 list 都在付 ~25s 全库扫盘税(full-review P0-4/G6)。PG 与磁盘的漂移
    属对账问题(后续 ops doctor),不由 list 承担。
    """
    config = Config.load(args.config_path)

    # Parse --filter-by up front so datasource conditions (field= / tables=) can
    # be pushed down into snapshot_store.list (SQL/GIN on the PG backend).
    # apply_filters still runs the full filter set below, so pushdown is a pure
    # pre-filter.
    filters: list[tuple[str, str, str]] | None = None
    if args.filter_by is not None:
        if not args.filter_by.strip():
            raise FilterError(["Empty filter expression."])
        filters = parse_filters(args.filter_by)

    field_pd, table_pd = _pushdown_params(filters) if filters else (None, None)
    metric_pd = _metric_pushdown(filters) if filters else []
    sort_pd = args.sort_by if args.sort_by in _SORTABLE_KEYS else None

    # repo.find 单条三表 LEFT JOIN(author/field/tables/metrics/status/sort
    # 下推;2026-07-09 退役 query_factors 的三次查 + 内存合并)。下面仍全量跑
    # 一遍 filter/status/sort/[:n],故下推纯为预筛,结果与不下推逐位等价。
    # limit 不下推,由此处 [:n] 在内存过滤后截断(P0-5 语义:先滤后截)。
    rows = FactorRepository(config).find(
        author=args.user, field=field_pd, table_glob=table_pd,
        metrics=metric_pd,
        status=args.status, sort_by=sort_pd,
    )
    # 兜底基线:默认 name ASC,下方 sort/filter 再叠加。
    rows.sort(key=lambda x: x.identity.name)

    if args.status:
        rows = [x for x in rows if x.status is not None and x.status.value == args.status]

    if filters is not None:
        rows = apply_filters(rows, filters)

    if args.sort_by and args.sort_by in _SORTABLE_KEYS:
        rows.sort(key=lambda x: metric_value(x.snapshot, args.sort_by) or 0, reverse=True)

    if args.n is not None:
        rows = rows[:args.n]

    return rows
