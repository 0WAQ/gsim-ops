#!/usr/bin/env python3
import os
import shutil
from pathlib import Path
from concurrent.futures import Future, ProcessPoolExecutor, as_completed 

from ops.infra.config import Config
from ops.infra.gsim.runner import Runner
from ops.utils.logger.log import *
from ops.utils.func import date_range
from ops.core.alpha.metadata import AlphaMetadata
from ops.core.alpha.metadatas import AlphaMetadatas
from ops.core.alpha.results.compliance import *
from ops.core.alpha.results.correlation import *
from ops.core.alpha.results.checkpoint import *
from ops.core.alpha.results.checkbias import *

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

        self.config.alpha_src.parent.mkdir(exist_ok=True)
        self.config.alpha_src.mkdir(exist_ok=True)
        self.config.alpha_dump.mkdir(exist_ok=True)
        self.config.alpha_pnl.mkdir(exist_ok=True)

        self.metadatas = AlphaMetadatas(self.config.dropbox_path_target, users, start, end, self.config, factor)

        self.compliance_checker = ComplianceChecker(config=self.config)
        self.correlation_checker = CorrelationChecker(config=self.config)
        self.checkpoint_checker = CheckpointChecker(config=self.config)
        self.checkbias_checker = CheckbiasChecker(config=self.config)


    def to_lib(self, factor: AlphaMetadata):
        try:
            factor.xml_config["gsim"]["Modules"]["Alpha"] = f"/mnt/storage/alphalib/alpha_src/{factor.name}/{factor.name}.py"
            factor.xml_config["gsim"]["Portfolio"]["Stats"]["@pnlDir"] = "/tmp/alphalib/alpha_pnl"
            factor.xml_config["gsim"]["Portfolio"]["Alpha"]["@dumpAlphaDir"] = "/tmp/alphalib/alpha_dump"

            shutil.move(factor.dir, self.config.alpha_src)
            shutil.move(factor.alpha_dir, self.config.alpha_dump)
            shutil.move(factor.pnl_file, self.config.alpha_pnl / factor.name)
        except Exception:
            ...

    def to_recycle(self, factor: AlphaMetadata, e: CheckFail):
        try:
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
            factor.xml_config["gsim"]['Portfolio']['Stats']['@dumpPnl'] = 'false'
            factor.xml_config["gsim"]['Universe']['@startdate'] = "20241201"
            factor.xml_config["gsim"]['Universe']['@enddate'] = "20241231"
            factor.save()

            # 1. Checkbias (Short Backtest)
            self.checkbias_checker.check(factor)
            info(f"  ✔  {factor.key} checkbias passed")

            # 2. Checkpoint
            self.checkpoint_checker.check(factor)
            info(f"  ✔  {factor.key} checkpoint passed")

            # 3. Clean and Change XML
            shutil.rmtree(factor.alpha_dir, ignore_errors=True)
            Path(factor.pnl_file).unlink(missing_ok=True)
            shutil.rmtree(factor.checkpoint_dir, ignore_errors=True)

            factor.xml_config["gsim"]["Universe"]["@startdate"] = "20150101"
            factor.xml_config["gsim"]["Universe"]["@enddate"]   = "20251231"
            factor.xml_config["gsim"]['Portfolio']['Stats']['@dumpPnl'] = 'true'
            factor.save()

            # 4. Long Backtest
            Runner.run_backtest(factor.xml_file, self.config)
            info(f"  ✔  {factor.key} long backtest succeed")

            # 5. Compliance
            self.compliance_checker.check(factor)
            info(f"  ✔  {factor.key} compliance passed")

            # 6. Correlation
            self.correlation_checker.check(factor)
            info(f"  ✔  {factor.key} correlation passed")
            
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

