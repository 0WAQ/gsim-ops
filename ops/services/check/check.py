import shutil
import traceback
import xmltodict
from datetime import datetime
from pathlib import Path
from concurrent.futures import Future, ProcessPoolExecutor, as_completed

from ops.infra.config import Config
from ops.infra.gsim.runner import Runner
from ops.infra.store import default_store
from ops.infra.lock import factor_lock, FactorLocked
from ops.services.list.metrics import update_metrics
from ops.utils.logger.log import *
from ops.core.alpha.metadata import AlphaMetadata
from ops.core.factormeta import FactorMeta
from ops.core.state import FactorRecord, FactorStatus, CheckRecord
from ops.core.alpha.results.compliance import *
from ops.core.alpha.results.correlation import *
from ops.core.alpha.results.checkpoint import *
from ops.core.alpha.results.checkbias import *

from .xml_prepare import *
from .checker.base import *
from .checker.compliance_checker import ComplianceChecker
from .checker.checkpoint_checker import CheckpointChecker
from .checker.checkbias_checker import CheckbiasChecker
from .checker.correlation_checker import CorrelationChecker

class CheckerPipeline:
    def __init__(self,
                 users: list[str] | None,
                 config_path: Path,
                 factor: str | None=None):

        self.config = Config.load(config_path)
        self.config_path = config_path
        self.config.alpha_src.parent.mkdir(exist_ok=True)
        self.config.alpha_src.mkdir(exist_ok=True)
        self.config.alpha_dump.mkdir(exist_ok=True)
        self.config.alpha_pnl.mkdir(exist_ok=True)

        self.metadatas = self._scan_factors(users, factor)
        for md in self.metadatas:
            prepare_for_initial(md, self.config)

        self.compliance_checker = ComplianceChecker(config=self.config)
        self.correlation_checker = CorrelationChecker(config=self.config)
        self.checkpoint_checker = CheckpointChecker(config=self.config)
        self.checkbias_checker = CheckbiasChecker(config=self.config)

    def _scan_factors(self, users: list[str] | None,
                      factor_name: str | None = None) -> list[AlphaMetadata]:
        """Scan staging/ (flat). Filter by submitted_by (users) and/or factor name.

        Each factor dir is expected to contain meta.json (created by submit).
        Factors without meta.json are skipped with a warning.
        """
        staging = self.config.staging
        if not staging.exists():
            return []

        candidates: list[Path] = []
        if factor_name is not None:
            d = staging / factor_name
            if d.is_dir():
                candidates.append(d)
        else:
            for d in staging.iterdir():
                if d.is_dir() and d.name.startswith("Alpha"):
                    candidates.append(d)

        mds: list[AlphaMetadata] = []
        for factor_dir in candidates:
            meta_path = factor_dir / "meta.json"
            if not meta_path.exists():
                warn(f"  ⚠  {factor_dir.name} 缺少 meta.json,跳过(请先 ops submit)")
                continue
            try:
                meta = FactorMeta.load(meta_path)
            except Exception as e:
                warn(f"  ⚠  {factor_dir.name} meta.json 解析失败: {e}")
                continue

            submitted_by = meta.submitted_by or meta.author or "unknown"
            if users and submitted_by not in users:
                continue

            date = str(meta.birthday) if meta.birthday else "unknown"
            md = AlphaMetadata(submitted_by, date, factor_dir, self.config)
            mds.append(md)

        return mds

    def _clean_pycache(self, root: Path) -> None:
        for p in root.rglob("__pycache__"):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)

    def _rewrite_module_path(self, dir: Path) -> None:
        """Rewrite XML's Modules.Alpha.@module to point to the .py inside `dir`,
        so the factor can be re-run from its new location.
        """
        xmls = list(dir.glob("*.xml"))
        pys = list(dir.glob("*.py"))
        if not xmls or not pys:
            return
        xml_file = xmls[0]
        cfg = xmltodict.parse(xml_file.read_text(encoding="utf-8"))
        modules_alpha = cfg.get("gsim", {}).get("Modules", {}).get("Alpha")
        if isinstance(modules_alpha, dict):
            modules_alpha["@module"] = str(pys[0])
            xml_file.write_text(
                xmltodict.unparse(cfg, pretty=True, encoding="utf-8", full_document=False),
                encoding="utf-8",
            )

    def to_lib(self, factor: AlphaMetadata):
        self._clean_pycache(factor.dir)
        shutil.move(factor.dir, self.config.alpha_src)
        self._rewrite_module_path(self.config.alpha_src / factor.dir.name)
        shutil.move(factor.alpha_dir, self.config.alpha_dump)
        shutil.move(factor.pnl_file, self.config.alpha_pnl / factor.name)

    def to_recycle(self, factor: AlphaMetadata, e: CheckFail):
        dst_dir = self.config.recycle / factor.key.user / e.stage / factor.name
        dst_dir.parent.mkdir(parents=True, exist_ok=True)
        if dst_dir.exists():
            shutil.rmtree(dst_dir)
        self._clean_pycache(factor.dir)
        shutil.move(factor.dir, dst_dir)
        self._rewrite_module_path(dst_dir)
        with open(dst_dir / "reason.txt", 'w') as f:
            f.write(str(e))

    def _ensure_record(self, factor: AlphaMetadata, store) -> None:
        if store.get(factor.name) is not None:
            return
        # Try to recover author/submitted_by from meta.json if present
        meta_path = factor.dir / "meta.json"
        author = factor.key.user
        submitted_by = factor.key.user
        submitted_at = datetime.now().isoformat(timespec="seconds")
        if meta_path.exists():
            try:
                meta = FactorMeta.load(meta_path)
                author = meta.author or author
                submitted_by = meta.submitted_by or submitted_by
                submitted_at = meta.submitted_at or submitted_at
            except Exception:
                pass
        now = datetime.now().isoformat(timespec="seconds")
        store.put(FactorRecord(
            name=factor.name,
            author=author,
            status=FactorStatus.SUBMITTED,
            updated_at=now,
            submitted_at=submitted_at,
            submitted_by=submitted_by,
        ))

    def run_one(self, factor: AlphaMetadata, i: int) -> str:
        """Returns one of: 'pass' | 'fail' | 'error' | 'locked'."""
        total = len(self.metadatas)
        prog  = (i + 1) / total
        bar   = f"[{i+1:>{len(str(total))}}/{total}] {prog:>6.1%}"
        print(f"{bar} checking ", end=""); highlight(f"{factor.key}")

        try:
            with factor_lock(factor.name):
                return self._run_one_locked(factor)
        except FactorLocked:
            warn(f"  ⚠  {factor.key} 已被另一个进程占用,跳过")
            return "locked"

    def _run_one_locked(self, factor: AlphaMetadata) -> str:
        store = default_store()
        self._ensure_record(factor, store)
        check = CheckRecord(started_at=datetime.now().isoformat(timespec="seconds"))
        store.transition(factor.name, FactorStatus.CHECKING)

        try:
            # 1. Checkbias (Short Backtest)
            prepare_for_checkbias(factor)
            self.checkbias_checker.check(factor)
            info(f"  ✔  {factor.key} checkbias passed")

            # 2. Checkpoint
            prepare_for_checkpoint(factor) # TODO: now, do nothing
            self.checkpoint_checker.check(factor)
            self.checkpoint_checker.clean(factor)
            info(f"  ✔  {factor.key} checkpoint passed")

            # 3. Compliance (Long Backtest)
            prepare_for_compliance(factor)
            self.compliance_checker.check(factor)
            info(f"  ✔  {factor.key} compliance passed")

            # 4. Correlation
            prepare_for_correlation(factor)
            self.correlation_checker.check(factor)
            info(f"  ✔  {factor.key} correlation passed")

            # 5. Archive
            metrics = Runner.run_simsummary(factor.pnl_file, self.config)
            prepare_for_archive(factor)
            self.to_lib(factor)
            if metrics:
                update_metrics(self.config_path, factor.name, metrics)

            now = datetime.now().isoformat(timespec="seconds")
            check.finished_at = now
            check.passed = True
            store.append_check(factor.name, check)
            store.transition(factor.name, FactorStatus.ACTIVE, entered_at=now)
            return "pass"

        except CheckSkip as e:
            warn(f"  ⚠  {factor.key} {e.stage} skipped. ({str(e)})")
            check.finished_at = datetime.now().isoformat(timespec="seconds")
            check.passed = None
            check.failed_stage = e.stage
            check.fail_reason = str(e)
            store.append_check(factor.name, check)
            # revert CHECKING → SUBMITTED, leave factor in staging for re-check
            store.transition(factor.name, FactorStatus.SUBMITTED)
            return "error"
        except CheckFail as e:
            error(f"  ✘  {factor.key} {e.stage} failed. ({str(e)})")
            prepare_for_recycle(factor) # TODO: now, do nothing
            self.to_recycle(factor, e)
            now = datetime.now().isoformat(timespec="seconds")
            check.finished_at = now
            check.passed = False
            check.failed_stage = e.stage
            check.fail_reason = str(e)
            store.append_check(factor.name, check)
            store.transition(factor.name, FactorStatus.REJECTED,
                             rejected_at=now,
                             last_fail_stage=e.stage,
                             last_fail_reason=str(e))
            return "fail"
        except Exception as e:
            # Environment / framework bug — NOT a factor problem.
            # Keep factor in staging, leave meta.json untouched, revert state to SUBMITTED.
            error(f"  ✘  {factor.key} unexpected error: {e}")
            traceback.print_exc()
            check.finished_at = datetime.now().isoformat(timespec="seconds")
            check.passed = None
            check.fail_reason = f"unexpected: {e}"
            store.append_check(factor.name, check)
            store.transition(factor.name, FactorStatus.SUBMITTED)
            return "error"

    def run(self):
        banner("因子检测")

        passed = failed = errored = locked = 0
        with ProcessPoolExecutor(max_workers=min(20, max(1, len(self.metadatas)))) as pool:
            futures: list[Future[str]] = []
            for i, factor in enumerate(self.metadatas):
                f = pool.submit(self.run_one, factor, i)
                futures.append(f)

            for f in as_completed(futures):
                match f.result():
                    case "pass":   passed  += 1
                    case "fail":   failed  += 1
                    case "error":  errored += 1
                    case "locked": locked  += 1

        banner("检测汇总")
        info(f"✔ 通过 : {passed:>4}")
        if failed > 0:
            error(f"✘ 未通过 : {failed:>4}")
        if errored > 0:
            warn(f"⚠ 异常 : {errored:>4}  (留在 staging,可 recheck)")
        if locked > 0:
            warn(f"⚠ 占用 : {locked:>4}  (被其他进程持有,跳过)")
        bottom()


def run_check(args):
    users: list[str] | None = [args.user] if args.user else None
    config_path: Path = args.config_path
    factor: str | None = args.factor_name

    pipline = CheckerPipeline(
        users=users,
        config_path=config_path,
        factor=factor
    )
    pipline.run()

