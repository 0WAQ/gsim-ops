import os
import shutil
from pathlib import Path
from concurrent.futures import Future, ProcessPoolExecutor, as_completed 

from ops.infra.config import Config
from ops.infra.gsim.runner import Runner
from ops.services.list.metrics import update_metrics
from ops.utils.logger.log import *
from ops.utils.func import date_range
from ops.core.alpha.metadata import AlphaMetadata
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
                 users: list[str], 
                 start: str, end: str,
                 config_path: Path,
                 factor: str | None=None):

        self.config = Config.load(config_path)
        self.config_path = config_path
        self.config.alpha_src.parent.mkdir(exist_ok=True)
        self.config.alpha_src.mkdir(exist_ok=True)
        self.config.alpha_dump.mkdir(exist_ok=True)
        self.config.alpha_pnl.mkdir(exist_ok=True)

        self._copy_from_dropbox(users, start, end)
        self.metadatas = self._scan_factors(users, start, end, factor)
        for md in self.metadatas:
            prepare_for_initial(md, self.config)

        self.compliance_checker = ComplianceChecker(config=self.config)
        self.correlation_checker = CorrelationChecker(config=self.config)
        self.checkpoint_checker = CheckpointChecker(config=self.config)
        self.checkbias_checker = CheckbiasChecker(config=self.config)

    def _copy_from_dropbox(self, users: list[str], start: str, end: str):
        for user in users:
            src_dir = self.config.dropbox_path / user
            root_dir = self.config.dropbox_path_target / user
            os.makedirs(root_dir, exist_ok=True)

            for date_str in date_range(start, end):
                src_date_dir = src_dir / date_str
                if not src_date_dir.is_dir():
                    continue
                dst_date_dir = root_dir / date_str
                if dst_date_dir.exists():
                    shutil.rmtree(dst_date_dir)
                shutil.copytree(src_date_dir, dst_date_dir)

    def _scan_factors(self, users: list[str], start: str, end: str,
                      factor_name: str|None=None) -> list[AlphaMetadata]:
        
        mds: list[AlphaMetadata] = []

        if factor_name is not None:
            assert start == end, "start must equal to end"
            for user in users:
                factor_dir = self.config.dropbox_path_target / user / start / factor_name
                if factor_dir.exists() and factor_dir.is_dir():
                    md = AlphaMetadata(user, start, factor_dir, self.config)
                    mds.append(md)
                    break
        else:
            for user in users:
                root_dir = self.config.dropbox_path_target / user
                for date in date_range(start, end):
                    date_path = root_dir / date
                    if not date_path.exists() or not date_path.is_dir():
                        continue
                    for factor_dir in date_path.iterdir():
                        if not factor_dir.name.startswith("Alpha"):
                            continue

                        md = AlphaMetadata(user, date, factor_dir, self.config)
                        mds.append(md)

        return mds

    def to_lib(self, factor: AlphaMetadata):
        shutil.move(factor.dir, self.config.alpha_src)
        shutil.move(factor.alpha_dir, self.config.alpha_dump)
        shutil.move(factor.pnl_file, self.config.alpha_pnl / factor.name)

    def to_recycle(self, factor: AlphaMetadata, e: CheckFail):
        dst_dir = self.config.recycle / factor.key.user \
                    / e.stage / factor.key.date / factor.name
        dst_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(factor.dir, dst_dir)
        with open(dst_dir / "reason.txt", 'w') as f:
            f.write(str(e))

    def run_one(self, factor: AlphaMetadata, i: int) -> bool:
        total = len(self.metadatas)
        prog  = (i + 1) / total
        bar   = f"[{i+1:>{len(str(total))}}/{total}] {prog:>6.1%}"
        print(f"{bar} checking ", end=""); highlight(f"{factor.key}")
        
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
            return True

        except CheckSkip as e:
            warn(f"  ⚠  {factor.key} {e.stage} skipped. ({str(e)})")
            return False
        except CheckFail as e:
            error(f"  ✘  {factor.key} {e.stage} failed. ({str(e)})")
            prepare_for_recycle(factor) # TODO: now, do nothing
            self.to_recycle(factor, e)
            return False
        except Exception as e:
            error(f"  ✘  {factor.key} failed. ({e})")
            return False

    def run(self):
        banner("因子检测")

        passed = failed = 0
        with ProcessPoolExecutor(max_workers=min(20, max(1, len(self.metadatas)))) as pool:
            futures: list[Future[bool]] = []
            for i, factor in enumerate(self.metadatas):
                f = pool.submit(self.run_one, factor, i)
                futures.append(f)

            for f in as_completed(futures):
                code = f.result()
                match code:
                    case True: passed += 1
                    case False: failed += 1

        banner("检测汇总")
        if passed >= 0:
            info(f"✔ 通过 : {passed:>4}")
        if failed > 0:
            error(f"✘ 未通过 : {failed:>4}")
        bottom()


def run_check(args):
    users: list[str] = [args.user]
    start: str = args.start_date
    end: str = args.end_date
    config_path: Path = args.config_path
    factor: str | None = args.factor_name

    pipline = CheckerPipeline(
        users=users, start=start, end=end,
        config_path=config_path,
        factor=factor
    )
    pipline.run()

