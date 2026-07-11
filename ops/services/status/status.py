"""status 查询编排 —— 零展示(2026-07-11 展示层上收:详情/表格渲染在
`ops/cli/status.py`,本模块只做数据访问)。

2026-07-09 阶段 3 塌缩:repo.get / repo.find(include_submitted=True ——
status 的语义是"任何记录",单条三表 JOIN 退役原 store.list + info_store.list
的内存合并)。"""
from ops.core.factor import Factor
from ops.infra.config import Config
from ops.infra.repository import FactorRepository


def query_one(args) -> Factor | None:
    """单因子全景(identity/state/snapshot)。None = factor_info 无记录;
    Factor.state 为 None = info 孤儿(有身份无 state 行,需对账)。"""
    config = Config.load(args.config_path)
    return FactorRepository(config).get(args.name)


def query_many(args) -> list[Factor]:
    """列表模式:任何记录(含 submitted),可按 author/status 过滤。"""
    config = Config.load(args.config_path)
    return FactorRepository(config).find(author=args.author, status=args.status,
                                         include_submitted=True)
