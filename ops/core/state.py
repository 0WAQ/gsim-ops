from dataclasses import asdict, dataclass, field
from enum import Enum


class FactorStatus(str, Enum):
    # 与 factor_state 的 chk_status CHECK 约束一一对应(DB 是权威)。DECAYING/RETIRED
    # 曾在此声明但 DB 拒收、无任何 transition 产生 —— 接口先行的幽灵状态,2026-07-07
    # 移除(full-review 第三部分 S10/G13);真要引入衰退生命周期时随 DB 约束一起加。
    SUBMITTED = "submitted"
    CHECKING  = "checking"
    ACTIVE    = "active"
    REJECTED  = "rejected"


# correlation 是唯一具有生命周期语义的 stage 名:它是 approve(多样性豁免)的
# 放行判据(last_fail_stage == CORRELATION)。定义放 core 使 approve 不必跨包
# import check(C3);stage 的顺序/路由/行为 SSOT 仍是 check/stages.py 的
# PIPELINE(其 correlation 行引用本常量,单一定义)。
CORRELATION = "correlation"


@dataclass
class CheckRecord:
    started_at: str
    finished_at: str | None = None
    passed: bool | None = None
    failed_stage: str | None = None
    fail_reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "CheckRecord":
        return cls(**d)


# factor_history.op 的合法值(与 DB chk_op CHECK 约束同一提交改,schema v2b)。
# 'entered' 是唯一非命令 op:任何写路径把 status 置 ACTIVE(check 归档 /
# approve 放行 / backfill 补录)都自动发射,是"入库了"这一事实的统一标记;
# 其余 op 一一对应写命令动作。CHECKING / revert-SUBMITTED 是瞬时态,无事件。
HISTORY_OPS = ("submit", "overwrite", "check", "approve",
               "restage", "cancel", "rm", "backfill", "entered")


@dataclass
class HistoryEvent:
    """factor_history 一行的领域形态 —— 全操作审计事件(schema v2b)。

    刻意无 FK:审计要活过 ops rm(指向已删因子的事件属预期,同名重提续写
    同一 name 的时间线)。started_at/passed/failed_stage/fail_reason 是
    op='check' 专属,其它 op 恒 None。actor='migration' = 回填合成。
    """
    name: str
    op: str
    at: str
    actor: str | None = None
    started_at: str | None = None
    passed: bool | None = None
    failed_stage: str | None = None
    fail_reason: str | None = None


@dataclass
class FactorRecord:
    """因子生命周期状态（纯状态机，不含身份信息）。

    author 已移到 FactorInfo，discovery_method 同理。
    本 record 只管状态转移: SUBMITTED -> CHECKING -> ACTIVE/REJECTED。

    rejected_at / last_fail_stage / last_fail_reason 已删(schema v2b):
    "最近一次失败"是 factor_history 的派生事实(op='check' AND passed=FALSE
    的最新行),读侧走 `Factor.last_fail`(repository 组装)。check_history
    保留为内存形态:PG 后端从事件表组装,json dev/test 后端仍随记录存。
    """
    name: str
    status: FactorStatus
    updated_at: str | None
    submitted_at: str | None = None
    entered_at: str | None = None
    version: int = 1
    check_history: list[CheckRecord] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "FactorRecord":
        d = dict(d)
        d["status"] = FactorStatus(d["status"])
        d.setdefault("version", 1)
        d["check_history"] = [CheckRecord.from_dict(c) for c in d.get("check_history", [])]
        # v2b 前的 json state 文件可能带已删除的三列,静默丢弃(dev/test 后端
        # 的旧文件兼容;PG 侧列已物理删除,不会走到)
        for legacy in ("rejected_at", "last_fail_stage", "last_fail_reason"):
            d.pop(legacy, None)
        return cls(**d)
