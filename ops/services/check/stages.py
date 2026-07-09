"""Check 流水线的 Stage 表 —— stage 身份的唯一事实源(full-review S11 / craft B2)。

此前一个 stage 的身份散在 ≥5 处、靠注释 "Must match" 手工同步:STAGES 元组、
_RETRYABLE_STAGES、on_reject 内嵌的 _LATE_STAGES、12 个异常子类各自硬编码的
stage 字符串、_run_one_locked 里 6 段复制粘贴的运行块。新增/改动 stage 要改
5 处,漏 1 处即静默路由错误。现在:**新增 stage = 在 PIPELINE 加一行**,
顺序、重试策略、产物保留策略、prepare、checker 工厂全部随行声明,其余派生。
"""
from collections.abc import Callable
from dataclasses import dataclass

from ops.core.alpha.metadata import AlphaMetadata
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

# correlation 是唯一被流水线之外引用的 stage 名:approve 只放行 correlation
# 失败(last_fail_stage 判定),archive 需捕获 correlation checker 的返回值落
# bcorr 快照。导出常量,别处不再手写字符串。
CORRELATION = "correlation"


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
