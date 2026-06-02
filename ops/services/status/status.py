from ops.core.state import FactorStatus, FactorRecord
from ops.infra.config import Config
from ops.infra.store import default_store
from ops.utils.logger.log import banner, bottom, info, warn, error, highlight


_STATUS_COLOR = {
    FactorStatus.SUBMITTED: info,
    FactorStatus.CHECKING:  highlight,
    FactorStatus.ACTIVE:    info,
    FactorStatus.REJECTED:  error,
    FactorStatus.DECAYING:  warn,
    FactorStatus.RETIRED:   warn,
    FactorStatus.DELETED:   warn,
}


def _print_one(rec: FactorRecord) -> None:
    color = _STATUS_COLOR.get(rec.status, info)
    color(f"  {rec.name:<40}  {rec.status.value:<10}  {rec.author:<10}  {rec.updated_at}")
    if rec.status == FactorStatus.REJECTED and rec.last_fail_stage:
        print(f"      ↳ {rec.last_fail_stage}: {rec.last_fail_reason}")


def _print_detail(rec: FactorRecord) -> None:
    print(f"name         : {rec.name}")
    print(f"author       : {rec.author}")
    print(f"status       : {rec.status.value}")
    print(f"submitted_at : {rec.submitted_at}")
    print(f"submitted_by : {rec.submitted_by}")
    print(f"entered_at   : {rec.entered_at}")
    print(f"rejected_at  : {rec.rejected_at}")
    print(f"updated_at   : {rec.updated_at}")
    if rec.last_fail_stage:
        print(f"last_fail    : {rec.last_fail_stage} — {rec.last_fail_reason}")
    if rec.check_history:
        print(f"check_history ({len(rec.check_history)}):")
        for i, c in enumerate(rec.check_history, 1):
            outcome = "PASS" if c.passed else ("FAIL" if c.passed is False else "SKIP")
            line = f"  [{i}] {c.started_at} → {c.finished_at}  {outcome}"
            if c.failed_stage:
                line += f"  ({c.failed_stage}: {c.fail_reason})"
            print(line)


def run_status(args) -> None:
    config = Config.load(args.config_path)
    store = default_store(config)
    name: str | None = args.name
    author: str | None = args.author
    status_filter: str | None = args.status

    if name is not None:
        rec = store.get(name)
        if rec is None:
            warn(f"未找到因子: {name}")
            return
        banner(f"因子状态 · {name}")
        _print_detail(rec)
        bottom()
        return

    status_enum = FactorStatus(status_filter) if status_filter else None
    records = store.list(author=author, status=status_enum)
    records.sort(key=lambda r: r.name)

    banner("因子状态")
    if not records:
        warn("没有匹配的因子记录")
    else:
        print(f"  {'name':<40}  {'status':<10}  {'author':<10}  updated_at")
        print(f"  {'-'*40}  {'-'*10}  {'-'*10}  {'-'*19}")
        for rec in records:
            _print_one(rec)
    bottom()
