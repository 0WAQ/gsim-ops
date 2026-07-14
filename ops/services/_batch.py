"""批量生命周期命令的共享骨架。

restage / approve / cancel / clear 共享同一骨架 —— 四份手抄会复制漂移
(如互斥检查漏在某一份)。本模块收敛公共骨架:

- `confirm_or_abort` — apt 风格确认(-y 跳过);
- `apply_locked` — per-factor 锁循环,统一持有四条纪律:
    1. **锁内复验**(TOCTOU 修复):确认提示挂起的
       几分钟里状态可能已被并发操作改变,action 内用 `SkipFactor` 声明"复验
       不通过就跳过",配合 `transition(expect=)` CAS 双保险;
    2. `FactorLocked` → warn + 跳过(check 正在跑等);
    3. `StateConflict`(CAS 失败)→ 按跳过处理,不算失败;
    4. 任何其它异常 → printer.error **且** logger.exception —— 写命令的失败
       必须在 ~/.cache/ops/logs/ 留诊断痕迹(否则失败零痕迹)。

各命令保留自己的:目标解析(resolve)、资格谓词、动作本体。`run_*` 返回
`BatchResult`,测试可以断言"正确拒绝"而非"跑完后状态没变"这种代理断言。
"""
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from ops.infra.lock import FactorLocked, factor_lock
from ops.infra.store import StateConflict
from ops.utils.log import logger
from ops.utils.printer import error, info, warn


class SkipFactor(Exception):
    """action 内声明'锁内复验不通过,本因子跳过'(带原因,计入 skipped)。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class BatchResult:
    done: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)   # (name, reason)
    failed: list[tuple[str, str]] = field(default_factory=list)    # (name, error)
    locked: list[str] = field(default_factory=list)

    def print_summary(self) -> None:
        info(f"  汇总: 成功={len(self.done)}  失败={len(self.failed)}  "
             f"占用={len(self.locked)}  跳过={len(self.skipped)}")


def confirm_or_abort(verb: str, n: int, yes: bool) -> bool:
    """apt 风格确认。返回 False 表示用户放弃(已打印'已取消')。"""
    if yes:
        return True
    ans = input(f"  确认 {verb} {n} 个因子? [y/N] ").strip().lower()
    if ans not in ("y", "yes"):
        info("  已取消")
        return False
    return True


def apply_locked(names: Iterable[str], config,
                 action: Callable[[str], None], *, verb: str) -> BatchResult:
    """对每个因子: factor_lock → action(name)。

    action 在锁内执行,应当先**重取记录并复验资格**(resolve 到这里之间世界
    可能已变),复验不过 raise SkipFactor(reason)。
    """
    res = BatchResult()
    for name in names:
        try:
            with factor_lock(name, config):
                action(name)
                res.done.append(name)
        except SkipFactor as e:
            warn(f"  ⚠ {name} 跳过: {e.reason}")
            res.skipped.append((name, e.reason))
        except StateConflict as e:
            warn(f"  ⚠ {name} 跳过: 确认期间状态已变 ({e})")
            res.skipped.append((name, f"状态已变: {e}"))
        except FactorLocked:
            warn(f"  ⚠ {name} 被另一个进程占用,跳过")
            res.locked.append(name)
        except Exception as e:
            error(f"  ✘ {name} 失败: {e}")
            logger.exception("{} failed factor={}", verb, name)
            res.failed.append((name, str(e)))
    res.print_summary()
    return res
