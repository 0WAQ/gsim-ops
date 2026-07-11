"""setup 执行引擎:跑注册表 → 结果列表(渲染归 cli 层,本模块零展示)。

apply=True(缺省,uv sync 语义)对不达标且带 fix 的项先幂等补建再**复检**,
fixed 以复检结果为准(fix 只补缺失,存在但形态错的项 fix 后依旧不达标 ——
手工处理);apply=False(--check)只读。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ops.utils.log import logger

from .checks import CHECKS, FAIL, OK, SKIP, CheckResult, Ctx

if TYPE_CHECKING:
    from ops.infra.config import Config


def run_setup(config: "Config", *, apply: bool = True,
              mounts: str = "", ctx: Ctx | None = None) -> list[CheckResult]:
    ctx = ctx or Ctx(config=config, mounts=mounts)
    results: list[CheckResult] = []
    for chk in CHECKS:
        try:
            passed, detail = chk.check(ctx)
        except Exception as e:  # 诊断命令:单项崩溃不拖垮整份清单
            logger.exception("setup check crashed: {}", chk.check_id)
            passed, detail = False, f"检查自身异常: {e}"

        fixed = False
        if not passed and apply and chk.fix is not None:
            try:
                chk.fix(ctx)
                passed, detail = chk.check(ctx)   # 复检定 fixed
                fixed = passed
            except Exception as e:
                detail = f"{detail};补建失败: {e}"

        status = OK if passed else chk.severity
        # skip 语义由检查函数自报(detail 以 "skip:" 开头,如非 PG 环境)
        if passed and detail.startswith("skip:"):
            status = SKIP
        results.append(CheckResult(
            check_id=chk.check_id, title=chk.title, status=status,
            detail=detail, fixable=chk.fix is not None, fixed=fixed,
        ))
    return results


def has_failures(results: list[CheckResult]) -> bool:
    return any(r.status == FAIL for r in results)
