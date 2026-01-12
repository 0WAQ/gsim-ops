#!/usr/bin/env python3
import shutil
from pathlib import Path
from concurrent.futures import Future, ProcessPoolExecutor, as_completed 

from ..common.config import Config
from ..common.runner import Runner
from ..common.logger.log import *
from ..common.alpha.metadata import AlphaMetadata
from ..common.alpha.metadatas import AlphaMetadatas
from ..common.alpha.results.compliance import *
from ..common.alpha.results.correlation import *
from ..common.alpha.results.checkpoint import *

from .checker.base import *
from .checker.modify_xml import do_main as modify_xml
from .checker.compliance_checker import ComplianceChecker
from .checker.checkpoint_checker import CheckpointChecker
from .checker.correlation_checker import CorrelationChecker


class CheckerPipeline:
    def __init__(self,
                 users: list[str], 
                 start: str, end: str,
                 config_path: Path,
                 factor: str | None=None):

        self.config = Config.load(config_path)
        
        # TODO:
        modify_xml(users, start, end, self.config)

        self.metadatas = AlphaMetadatas(self.config.dropbox_path_target, users, start, end, factor)

        self.compliance_checker = ComplianceChecker(config=self.config)
        self.correlation_checker = CorrelationChecker(config=self.config)
        self.checkpoint_checker = CheckpointChecker(config=self.config)


    def to_lib(self, factor: AlphaMetadata):
        try:
            shutil.move(factor.dir, self.config.alpha_src)
            shutil.move(factor.alpha_dir, self.config.alpha_dump)
            shutil.move(factor.pnl_file, self.config.alpha_pnl)
        except Exception:
            ...

    def to_recycle(self, factor: AlphaMetadata, e: CheckFail):
        try:
            # TODO:
            from .checker.correlation_checker import CorrelationFail
            if isinstance(e, CorrelationFail):
                return 1

            dst_dir = self.config.recycle / factor.key.user \
                        / e.stage / factor.key.date / factor.name
            dst_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(factor.dir, dst_dir)
            with open(dst_dir / "reason.txt", 'w') as f:
                f.write(str(e))
        except Exception:
            ...

    def run_one(self, factor: AlphaMetadata, i: int) -> bool:
        total = len(self.metadatas)
        prog  = (i + 1) / total
        bar   = f"[{i+1:>{len(str(total))}}/{total}] {prog:>6.1%}"
        print(f"{bar} checking ", end=""); highlight(f"{factor.key}")
        
        try:
            # 1. Short Backtest
            Runner.run_backtest(factor.xml_file, self.config)
            info(f"  ✔  {factor.key} short backtest succeed")

            # 2. Checkpoint
            self.checkpoint_checker.check(factor)
            info(f"  ✔  {factor.key} checkpoint passed")

            # 3. Clean and Change XML
            shutil.rmtree(factor.alpha_dir, ignore_errors=True)
            Path(factor.pnl_file).unlink(missing_ok=True)
            shutil.rmtree(factor.checkpoint_dir, ignore_errors=True)

            factor.xml_config["gsim"]["Universe"]["@startdate"] = "20150101"
            factor.xml_config["gsim"]["Universe"]["@enddate"]   = "20241231"
            factor.save()

            # 4. Long Backtest
            Runner.run_backtest(factor.xml_file, self.config)
            info(f"  ✔  {factor.key} long backtest succeed")

            # 5. Compliance
            self.compliance_checker.check(factor)
            info(f"  ✔  {factor.key} compliance passed")

            # 6. Correlation
            self.correlation_checker.check(factor)
            info(f"✔  {factor.key} correlation passed")
            
            # 7. Archive
            self.to_lib(factor)
            return True
        except CheckSkip as e:
            warn(f"  ⚠  {factor.key} {e.stage} skipped. ({str(e)})")
            return False
        except CheckFail as e:
            error(f"  ✘  {factor.key} {e.stage} failed. ({str(e)})")
            self.to_recycle(factor, e)
            return False
        except Exception as e:
            return False

    def run(self):
        banner("因子检测")

        passed = failed = 0
        with ProcessPoolExecutor(max_workers=min(20, len(self.metadatas))) as pool:
            futures: list[Future[bool]] = []
            for i, factor in enumerate(self.metadatas):
                f = pool.submit(self.run_one, factor, i)
                futures.append(f)

            for f in as_completed(futures):
                code = f.result()
                match code:
                    case 0: passed += 1
                    case 1: failed += 1

        banner("检测汇总")
        info(f"✔ 通过 : {passed:>4}")
        error(f"✘ 未通过 : {failed:>4}")
        bottom()


def run_entry(args):
    users: list[str] = [args.user]
    start: str = args.start_date
    end: str = args.end_date
    config_path: Path = args.config_path
    factor: str | None = args.factor_name

    notifier = CheckerPipeline(
        users=users, start=start, end=end,
        config_path=config_path,
        factor=factor
    )
    notifier.run()

