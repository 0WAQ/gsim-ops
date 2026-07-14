"""doctor 数据类型:Finding / FamilyResult / Inventory / FixPlan。

数据面对账的检查返回**发现列表**,不是 setup 那种 (bool, detail) —— 8000+
因子规模下汇总表必须有分母(population)、明细必须可截断可 JSON。
severity 落在 **kind 级**(同族里"库内源码丢失"是 FAIL、"crash 残渣"是
WARN,族级一刀切会让 cron 退出码变寻呼噪音)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

FAIL, WARN = "fail", "warn"


class FamilySkip(RuntimeError):
    """scan 主动放弃整族(如疑似 config 错配)—— engine 记 skip_reason,
    绝不产出任何 fixable finding。"""

# fix 执行结局(guards.execute 返回;记账进 FamilyResult)
FIXED, LOCKED, VANISHED, BLOCKED, ERROR = (
    "fixed", "locked", "vanished", "blocked", "error")


@dataclass(frozen=True)
class Finding:
    """单条漂移。frozen —— 修复结局记在 FamilyResult.fix_log,不改发现本身。"""
    name: str            # 因子名(pack-tmp 等无因子名时用文件名)
    family: str
    kind: str
    severity: str        # fail | warn(kind 级)
    reason: str
    fixable: bool = False
    path: str = ""       # 展示/审计用主要落点;执行时 guards 现场重拼,不信这里
    ref: str = ""        # fixer 解析用引用(池 kind / tmp 文件名等)
    action: str = ""     # report-only 族的转介可贴命令行


@dataclass
class FamilyResult:
    family_id: str
    title: str
    scope: str                      # pg | global | host
    population: int = 0             # 检查对象分母
    findings: list[Finding] = field(default_factory=list)
    skip_reason: str = ""           # 整族 skip(区不可用/无权),findings 为空
    # 修复记账(--fix 时填;(finding, outcome, err))
    fix_log: list[tuple[Finding, str, str]] = field(default_factory=list)

    def count(self, outcome: str) -> int:
        return sum(1 for _, o, _ in self.fix_log if o == outcome)

    @property
    def fixed(self) -> int:
        return self.count(FIXED)

    def residual(self, severity: str) -> int:
        """修复记账后仍然成立的漂移数(fixed/vanished 视为已消)。"""
        gone = {f.name + f.kind + f.ref
                for f, o, _ in self.fix_log if o in (FIXED, VANISHED)}
        return sum(1 for f in self.findings
                   if f.severity == severity and (f.name + f.kind + f.ref) not in gone)


@dataclass(frozen=True)
class FixPlan:
    """fix 的动作面,注册表必填字面量 —— 确认文案逐字打印它,"打印的就是
    执行的"。写不出这三句话的 fix 不允许注册。"""
    action: str      # unlink | rmtree | discard_snapshot(白名单,guards 只认这三个)
    target: str      # 删什么/在哪(一句话)
    keeps: str       # 不碰什么(一句话)


@dataclass(frozen=True)
class Entry:
    """盘面区浅层条目(采集期一次 stat,判定纯函数只吃这个)。"""
    name: str
    is_dir: bool
    is_symlink: bool = False
    mtime: float = 0.0


@dataclass
class Area:
    """一个盘面区的浅层清单。error 非空 = 区不可用(依赖它的族整族 skip)。"""
    root: Path
    entries: list[Entry] = field(default_factory=list)
    error: str = ""

    @property
    def names(self) -> set[str]:
        return {e.name for e in self.entries}


@dataclass
class Inventory:
    """采集产物:一次 PG 全集 + 各区浅 iterdir。判定函数 (Inventory) ->
    list[Finding] 是纯函数,单测零 I/O 手造这个即可。"""
    factors: dict           # name -> Factor(repo.find(include_submitted=True))
    areas: dict[str, Area]  # alpha_src / staging / alpha_pnl / alpha_feature /
                            # pool_automated / pool_manual / dump_local
    hostname: str = ""
    now: float = 0.0        # 采集时刻(pack-tmp 的 mtime 阈值判定用,保持纯函数)
    # name → 最近一次 check 事件的 at(测得快照对账的期望值来源)
    last_check_at: dict = field(default_factory=dict)
