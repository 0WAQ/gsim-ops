"""Check 流水线的 Stage 表 —— stage 身份的唯一事实源。

一个 stage 的身份(顺序、重试策略、产物保留策略、prepare、checker 工厂)若
散在多处手工同步,新增/改动 stage 漏一处即静默路由错误。现在:**新增 stage
= 在 PIPELINE 加一行**,其余全部随行派生。
"""
from collections.abc import Callable
from dataclasses import dataclass

from ops.core.alpha.metadata import AlphaMetadata

# CORRELATION 是唯一有生命周期语义的 stage 名(approve 的放行判据),定义在
# core/state.py(消 approve→check 跨包边);PIPELINE 的 correlation 行引用它
# —— 单一定义,顺序/路由 SSOT 仍是本表。此处 re-export 保住 check 包内
# `from .stages import CORRELATION` 的存量路径。
from ops.core.state import CORRELATION
from ops.infra.config import Config

from .checker.base import Checker
from .checker.checkbias_checker import CheckbiasChecker
from .checker.checkpoint_checker import CheckpointChecker
from .checker.compliance_checker import ComplianceChecker
from .checker.correlation_checker import CorrelationChecker
from .checker.long_backtest_checker import LongBacktestChecker
from .checker.validate_checker import ValidateChecker
from .xml_prepare import (
    prepare_for_checkbias,
    prepare_for_checkpoint,
    prepare_for_long_backtest,
    prepare_for_validate,
)


@dataclass(frozen=True)
class Stage:
    """一个 check stage 的全部身份信息。

    - make_checker: checker 工厂(生产路径由此构造;测试经 DI 注入 fake)
    - prepare: 跑 checker 前对 staging XML 的窗口/开关改写(None = 无需准备)
    - retryable: 失败按环境/配置问题处理(revert SUBMITTED 留 staging 待重跑),
      而非因子质量问题(REJECTED)
    - keep_artifacts_on_fail: 失败仍保留 pnl/dump(晚期 stage 数据完整,
      有分析价值;早期 stage 数据不完整,清掉)
    """
    name: str
    make_checker: Callable[[Config], Checker]
    prepare: Callable[[AlphaMetadata], None] | None = None
    retryable: bool = False
    keep_artifacts_on_fail: bool = False


PIPELINE: tuple[Stage, ...] = (
    Stage("validate", ValidateChecker, prepare_for_validate, retryable=True),
    Stage("checkbias", CheckbiasChecker, prepare_for_checkbias),
    Stage("checkpoint", CheckpointChecker, prepare_for_checkpoint),
    Stage("long_backtest", LongBacktestChecker, prepare_for_long_backtest, retryable=True),
    Stage("compliance", ComplianceChecker, keep_artifacts_on_fail=True),
    Stage(CORRELATION, CorrelationChecker, keep_artifacts_on_fail=True),
)

# 派生视图 —— 全部由 PIPELINE 生成,不再手抄
STAGES: tuple[str, ...] = tuple(s.name for s in PIPELINE)
RETRYABLE_STAGES: frozenset[str] = frozenset(s.name for s in PIPELINE if s.retryable)
KEEP_ARTIFACTS_STAGES: frozenset[str] = frozenset(
    s.name for s in PIPELINE if s.keep_artifacts_on_fail)
