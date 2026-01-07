#!/usr/bin/env python3
"""
因子检测并发送通知脚本 (配置文件版 - 合规性 → 相关性 → 断点)
检测顺序：
    1. 合规性检测 (所有因子)
    2. 相关性检测 (通过合规性的因子)
    3. 断点检测 (通过合规性和相关性的因子)
"""
import shutil
from pathlib import Path
from concurrent.futures import Future, ProcessPoolExecutor, as_completed 

from ..common.config import Config
from ..common.runner import Runner
from ..common.logger.log import *
from ..common.alpha.metadata import AlphaMetadata
from ..common.alpha.metadatas import AlphaMetadatas
from ..common.alpha.report import AlphaReport
from ..common.alpha.results.compliance import *
from ..common.alpha.results.correlation import *
from ..common.alpha.results.checkpoint import *

from .checker.base import *
from .checker.modify_xml import do_main as modify_xml
from .checker.compliance_checker import ComplianceChecker
from .checker.checkpoint_checker import CheckpointChecker, CheckpointSkip, CheckpointFail
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

    def classify(self,
                passed: list[tuple[AlphaReport, str, str]],
                failed: list[tuple[AlphaReport, str, str]],
                skipped: list[tuple[AlphaReport, str, str]]) -> None:
        """
        将因子按检测结果归类到对应目录
        """
        src     = self.config.alpha_src
        dump    = self.config.alpha_dump
        pnl     = self.config.alpha_pnl
        recycle = self.config.recycle

        for d in (src, dump, pnl, recycle):
            d.mkdir(parents=True, exist_ok=True)

        banner("因子归档")

        # -------------- 通过 --------------
        if passed:
            info(f"🚀  通过因子  ({len(passed)} 个)")
            for report, _, _ in passed:
                factor = self.metadatas[report.key]
                try:
                    shutil.move(factor.dir, src)
                    shutil.move(factor.alpha_dir, dump)
                    shutil.move(factor.pnl_file, pnl)
                except Exception as e:
                    error(f"  ✘ 移动失败 {factor.name}: {e}")
        else:
            warn("⚠   无通过因子")

        # -------------- 失败 --------------
        if failed:
            warn(f"🗑️  失败因子  ({len(failed)} 个)")
            for report, path, reason in failed:
                factor = self.metadatas[report.key]
                dst_dir = recycle / factor.key.user / path / factor.key.date / factor.name
                dst_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(factor.dir, dst_dir)
                reason_file = dst_dir / "reason.txt"
                with open(reason_file, 'w') as f:
                    f.write(reason)
        else:
            info("🚀   无失败因子")

        # -------------- 跳过 --------------
        if skipped:
            warn(f"⏭️  跳过因子  ({len(skipped)} 个) —— 不做归档")
        else:
            warn("🚀   无跳过因子")

        # -------------- 汇总 --------------
        banner("归档完成")
        info(f"✔ 入库  : {len(passed):>4}")
        error(f"✘ 回收  : {len(failed):>4}")
        warn(f"⚠ 跳过  : {len(skipped):>4}")
        bottom()

    def classify_one(self, factor: AlphaMetadata):
        ...

    def run_one(self, factor: AlphaMetadata, i: int) -> tuple[int, AlphaReport, str, str] | None:
        total = len(self.metadatas)
        prog  = (i + 1) / total
        bar   = f"[{i+1:>{len(str(total))}}/{total}] {prog:>6.1%}"
        print(f"{bar} checking ", end=""); highlight(f"{factor.key}")

        report = AlphaReport(factor.key)
        
        try:
            # 1. Short Backtest
            ok, err = Runner.run_backtest(factor.xml_file, self.config)
            if not ok:
                error(f"  ↘  {factor.key} 短区间回测失败  {err}")
            info(f"  ✔  {factor.key} short backtest succeed")

            # 2. Checkpoint
            report.checkpoint_result = self.checkpoint_checker.check(factor)
            info(f"  ✔  {factor.key} checkpoint passed")

            # 3. Clean and Change XML
            shutil.rmtree(factor.alpha_dir, ignore_errors=True)
            Path(factor.pnl_file).unlink(missing_ok=True)
            shutil.rmtree(factor.checkpoint_dir, ignore_errors=True)

            factor.xml_config["gsim"]["Universe"]["@startdate"] = "20150101"
            factor.xml_config["gsim"]["Universe"]["@enddate"]   = "20241231"
            factor.save()

            # 4. Long Backtest
            ok, err = Runner.run_backtest(factor.xml_file, self.config)
            if not ok:
                error(f"  ✘  {factor.key} 长区间回测失败 {err}")
            info(f"  ✔  {factor.key} long backtest succeed")

            # 5. Compliance
            report.compliance_result = self.compliance_checker.check(factor)
            info(f"  ✔  {factor.key} compliance passed")

            # 6. Correlation
            report.correlation_result = self.correlation_checker.check(factor)
            info(f"  ✔  {factor.key} correlation passed")

        except CheckSkip as e:
            warn(f"  ⚠  {factor.key} {e.stage} skipped. ({str(e)})")
        except CheckFail as e:
            error(f"  ✘  {factor.key} {e.stage} failed. ({str(e)})")
        except Exception as e:
            ...


    def run(self):
        banner("因子检测")

        passed, failed, skipped = [], [], []
        with ProcessPoolExecutor(max_workers=min(20, len(self.metadatas))) as pool:
            futures: list[Future[tuple[int, AlphaReport, str, str] | None]] = []
            for i, factor in enumerate(self.metadatas):
                f = pool.submit(self.run_one, factor, i)
                futures.append(f)

            for f in as_completed(futures):
                ...
                # code, report, path, err = f.result() or (2, report, path, err)
                # (passed if code == 2 else failed if code == 1 else skipped).append((report, path, err))

        banner("检测汇总")
        info(f"✔ 通过 : {len(passed):>4}")
        error(f"✘ 失败 : {len(failed):>4}")
        warn(f"⚠ 跳过 : {len(skipped):>4}")
        bottom()

        # self.classify(passed, failed, skipped)


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

