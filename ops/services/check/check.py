import multiprocessing as mp
import shutil
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path

from ops.core.alpha.metadata import AlphaMetadata
from ops.core.alpha.results.correlation import CorrResult
from ops.core.datasource import (
    build_npy_index,
    parse_datasources,
    resolve_tables,
)
from ops.core.factor import FactorIdentity, FactorSnapshot
from ops.core.factormeta import FactorMeta
from ops.core.paths import META_FILENAME, FactorPaths
from ops.core.state import CheckRecord, FactorStatus
from ops.infra.config import Config
from ops.infra.gsim.runner import Runner
from ops.infra.lock import FactorLocked, factor_lock
from ops.infra.repository import FactorRepository
from ops.utils.clock import now_iso
from ops.utils.factor_dir import clean_pycache, rewrite_module_path
from ops.utils.live_table import LiveDriver, Status, make_factor_rows
from ops.utils.log import STDERR_SINK_ID, logger
from ops.utils.printer import _console as _printer_console
from ops.utils.printer import banner, bottom, error, info, warn

from .checker.base import Checker, CheckFail, CheckSkip
from .report import write_check_report
from .stages import (
    CORRELATION,
    KEEP_ARTIFACTS_STAGES,
    PIPELINE,
    RETRYABLE_STAGES,
    STAGES,
)
from .xml_prepare import prepare_for_archive, prepare_for_initial


class CheckerPipeline:
    def __init__(self,
                 users: list[str] | None,
                 config_path: Path,
                 factor: str | None = None,
                 checkers: dict[str, "Checker"] | None = None):

        self.config = Config.load(config_path)
        self.config_path = config_path
        self.config.alpha_src.parent.mkdir(exist_ok=True)
        self.config.alpha_src.mkdir(exist_ok=True)
        self.config.alpha_dump.mkdir(exist_ok=True)
        self.config.alpha_pnl.mkdir(exist_ok=True)
        self.config.pnl_automated.mkdir(parents=True, exist_ok=True)
        self.config.pnl_manual.mkdir(parents=True, exist_ok=True)

        # kept for report file naming (see .report.write_check_report)
        self._user = users[0] if users else None
        self._factor = factor

        self.metadatas = self._scan_factors(users, factor)
        for md in self.metadatas:
            prepare_for_initial(md, self.config)

        # Checkers are dependency-injected: pass `checkers` to substitute fakes
        # in tests. Unset (production) → construct the real gsim-backed checkers
        # via the PIPELINE stage table, behavior unchanged.
        injected = checkers or {}
        self.checkers: dict[str, Checker] = {
            s.name: injected.get(s.name) or s.make_checker(self.config)
            for s in PIPELINE
        }


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
            d = FactorPaths.of(factor_name, self.config).staging
            if d.is_dir():
                candidates.append(d)
        else:
            for d in staging.iterdir():
                if d.is_dir() and d.name.startswith("Alpha"):
                    candidates.append(d)

        mds: list[AlphaMetadata] = []
        for factor_dir in candidates:
            meta_path = factor_dir / META_FILENAME
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

    def _repo(self) -> FactorRepository:
        """按需构造(不挂 self):worker 进程 fork 自父进程,父进程实例若已
        materialize 懒加载 store,其 PG 池对象在子进程里是死的(worker 线程
        不随 fork 存活,pg.py 的 fork 钩子只重置注册表救不了已捏在手里的引用)。
        每次现构造 + get_pool 按 (pid, conninfo) 去重 → 子进程拿到自己的池。"""
        return FactorRepository(self.config)

    def _persist_derived(self, factor: AlphaMetadata, metrics, corr_result,
                         measured_at: str) -> None:
        """把本次 check 测得的表现写入 factor_snapshot(**测得快照**:
        最近一次 check 测得的表现 —— pass 与 correlation/compliance 失败都写,
        入库见证已全权归 entered_at/entered 事件)。

        必须在盘面搬运(to_lib / on_reject)之前调 —— datasources 依赖
        factor.py_file(此时仍在 staging)。各组独立 try,互不阻断主流程。

        落库半边(snapshot_at = measured_at + stale 自愈替换)归
        repo.attach_snapshot;本方法只负责**采集**(metrics / datasources /
        bcorr / delay —— check 期领域知识)。
        """
        # 准备各组数据
        ret = shrp = mdd = tvr = fitness = None
        if metrics:
            ret, shrp, mdd, tvr, fitness = (
                metrics.ret, metrics.shrp, metrics.mdd, metrics.tvr, metrics.fitness
            )

        fields = tables = None
        try:
            fields = parse_datasources(factor.py_file)
            tables = resolve_tables(fields, build_npy_index(self.config.nio_data_path))
        except Exception:
            logger.exception("parse datasources failed factor={}", factor.name)

        max_bcorr = max_bcorr_factor = None
        if corr_result is not None:
            max_bcorr = corr_result.max_bcorr
            max_bcorr_factor = corr_result.max_bcorr_factor

        try:
            self._repo().attach_snapshot(FactorSnapshot(
                name=factor.name,
                ret=ret,
                shrp=shrp,
                mdd=mdd,
                tvr=tvr,
                fitness=fitness,
                fields=fields,
                tables=tables,
                delay=factor.delay,
                max_bcorr=max_bcorr,
                max_bcorr_factor=max_bcorr_factor,
            ), measured_at=measured_at)
        except Exception:
            logger.exception("persist snapshot failed factor={}", factor.name)

    def to_lib(self, factor: AlphaMetadata):
        """归档入库:搬运 + @module 重指 + pnl 分流全部收编 repo.archive
        (身份兜底断言在 repo.archive;第一道闸在 run_one 入口)。"""
        self._repo().archive(
            factor.name,
            src_dir=factor.dir,
            dump_dir=factor.alpha_dir,
            pnl_file=factor.pnl_file,
            discovery_method=factor.discovery_method,
        )

    def on_reject(self, factor: AlphaMetadata, failed_stage: str):
        """因子质量失败:src 归档到 alpha_src(与 ACTIVE 同库,状态由 state 区分),
        按失败阶段决定产物保留,最后清掉 staging 原物。不再写 recycle 目录。
        """
        clean_pycache(factor.dir)

        # alpha_src: 从 staging 复制一份(REJECTED 与 ACTIVE 的 src 都在 alpha_src,
        # 状态靠 state 区分,不靠目录位置)
        paths = FactorPaths.of(factor.name, self.config)
        if paths.src.exists():
            shutil.rmtree(paths.src)
        shutil.copytree(factor.dir, paths.src)
        rewrite_module_path(paths.src)

        # 产物保留策略由 Stage 表的 keep_artifacts_on_fail 声明:
        # 晚期 stage(compliance/correlation)数据完整,保留 pnl + dump 供分析;
        # 早期 stage(checkbias/checkpoint)数据不完整,清掉 dump + feature。
        if failed_stage in KEEP_ARTIFACTS_STAGES:
            # 保留 pnl
            if factor.pnl_file.exists():
                shutil.copy2(factor.pnl_file, paths.pnl)
            # 保留 dump
            if factor.alpha_dir.exists() and not paths.dump.exists():
                shutil.move(str(factor.alpha_dir), str(paths.dump))
        else:
            # checkbias/checkpoint: 清掉 dump + feature(短期数据不完整)
            if paths.dump.exists():
                shutil.rmtree(paths.dump)
            for f in paths.features:
                if f.exists():
                    f.unlink()

        # 清掉 staging 原物(src 已进 alpha_src,不再需要归档副本)
        shutil.rmtree(factor.dir, ignore_errors=True)

    def _ensure_record(self, factor: AlphaMetadata, repo: FactorRepository) -> None:
        if repo.record(factor.name) is not None:
            return
        # state record 缺失（crash 恢复 / 直接 check staging 未经 submit）时补建。
        # repo.register 原子写 info(身份)+ state(状态)一个事务(原先顺序两次
        # 调用,崩在中间留半截;json dev/test 后端只写 state,不再硬碰 PG info)。
        meta_path = factor.dir / META_FILENAME
        author = factor.key.user
        discovery_method = factor.discovery_method
        submitted_at = now_iso()
        if meta_path.exists():
            try:
                meta = FactorMeta.load(meta_path)
                author = meta.author or author
                discovery_method = meta.discovery_method or discovery_method
                submitted_at = meta.submitted_at or submitted_at
            except Exception:
                pass
        if discovery_method not in ("automated", "manual"):
            # factor_info.discovery_method NOT NULL:submit 入口早有硬校验,能走到
            # 这里的是 pre-guard 遗留/手工放置的 staging 残留 —— 状态写入前显式拒绝
            # (run_one preamble 臂接住 → error),不让 DB 约束错误当路由。
            # 重新 ops submit 补全后再检。
            raise RuntimeError(
                f"{factor.name} discovery_method 缺失/非法"
                f"({discovery_method!r},须为 automated/manual)"
                "—— 重新 ops submit 以补全身份")
        repo.register(
            FactorIdentity(
                name=factor.name,
                author=author,
                discovery_method=discovery_method,
                created_at=submitted_at,
            ),
            submitted_at=submitted_at,
            op="submit",  # 补建 = 补记那次丢失的 submit(crash 自愈语义)
        )

    def run_one(self, factor: AlphaMetadata, i: int, q) -> str:
        """Returns one of: 'pass' | 'fail' | 'error' | 'locked'.

        Stage events are emitted via the queue `q` so the parent's LiveDriver
        can render them. The return string is also still consumed by the
        parent for counter accumulation.
        """
        # 身份不变量:staging 目录名必须等于 XML @id(submit 的 normalize_factor_xml
        # 强制 @id := 目录名)。发散时(手工放置 / 中断 submit 留下的 stale XML)
        # state/lock/归档落点全键在 @id 上,而 staging 原物键在目录名上 —— 归档会
        # rmtree alpha_src/<@id>,那可能是**另一个在库因子的唯一源码**。必须在任何
        # 状态写入(_ensure_record/transition)之前整单拒绝;残留由人工重新
        # ops submit(或 ops clear)处理。
        if factor.dir.name != factor.name:
            logger.error(
                "身份发散拒绝 check: staging 目录 {} != XML @id {} "
                "(stale/手工 XML;重新 ops submit 以恢复 @id := 目录名 的不变量)",
                factor.dir.name, factor.name)
            q.put(("done", factor.name, "error",
                   f"! 目录名 {factor.dir.name} != @id {factor.name}", "red"))
            return "error"
        try:
            with factor_lock(factor.name, self.config):
                return self._run_one_locked(factor, q)
        except FactorLocked:
            q.put(("done", factor.name, "locked", "🔒 已被另一个进程占用", "yellow"))
            return "locked"
        except Exception as e:
            # 前置段兜底:_run_one_locked 的 try 只包 stage 循环,
            # 之前的 _ensure_record/transition(以及锁连接本身)在 PG 不可达 /
            # 空库时抛的异常会直接穿透 ProcessPool —— 父进程的 LiveDriver 无法
            # 把崩溃的 future 映射回因子名。
            # worker 是唯一知道自己因子名的地方,在此归因并保证 done 事件必发。
            logger.exception("check preamble crashed factor={}", factor.key)
            q.put(("done", factor.name, "error",
                   f"! pre-check: {str(e)[:80]}", "red"))
            return "error"

    def _run_one_locked(self, factor: AlphaMetadata, q) -> str:
        # 全部 state 读写经 Repository:统一 schema 懒引导(空库上裸 store 的
        # SELECT 直接 UndefinedTable)与 fork 池安全(见 _repo 注)。
        repo = self._repo()
        self._ensure_record(factor, repo)
        check = CheckRecord(started_at=now_iso())
        repo.transition(factor.name, FactorStatus.CHECKING)

        # 清上一轮 check 的 checkpoint 残留(锁内,开跑前)。long_backtest 写的
        # checkpoint 无人善后(CheckpointChecker.clean 只清它之前的),restage
        # 重检时 checkbias 的 gsim 会 load 上一轮全历史窗口的残留,
        # StatsSimpleV6.checkpointLoad 直接崩 io.UnsupportedOperation
        # (见 JOURNAL PV5)。本轮内 checkbias → checkpoint
        # 的断点续跑不受影响 —— 那些 checkpoint 在本次 wipe 之后才写。
        shutil.rmtree(factor.checkpoint_dir, ignore_errors=True)

        # Track which stage is currently running so the catch-all except clauses
        # can mark it failed in the Live table.
        current_stage: str | None = None

        def _emit_stage_start(stage: str) -> None:
            nonlocal current_stage
            current_stage = stage
            q.put(("stage_start", factor.name, stage))

        def _emit_stage_done(stage: str, status: Status) -> None:
            nonlocal current_stage
            current_stage = None
            q.put(("stage_done", factor.name, stage, status))

        try:
            # Stage 顺序、prepare、checker 均由 PIPELINE 表驱动(stages.py)。
            # prepare 失败不再被吞:异常落到下方 unexpected-error 臂
            # (revert SUBMITTED + 完整日志),不会拿着错误窗口继续跑。
            corr_result = None
            for stage in PIPELINE:
                _emit_stage_start(stage.name)
                if stage.prepare is not None:
                    stage.prepare(factor)
                checker = self.checkers[stage.name]
                result = checker.check(factor)
                checker.clean(factor)
                if stage.name == CORRELATION:
                    corr_result = result
                _emit_stage_done(stage.name, Status.PASSED)

            # Archive — simsummary + mark ACTIVE (设置 entered_at) + persist snapshot + move to lib
            #    (no live column; folded into outcome)
            metrics = Runner.run_simsummary(factor.pnl_file, self.config)

            # 先设置 entered_at (入库时间)，snapshot_at 依赖这个时间戳
            now = now_iso()
            check.finished_at = now
            check.passed = True
            repo.append_check(factor.name, check)
            repo.transition(factor.name, FactorStatus.ACTIVE, entered_at=now)

            # 再写入测得快照(measured_at = 同一个 now → 对 pass 因子
            # snapshot_at 仍与 entered_at 逐字符相等)
            self._persist_derived(factor, metrics, corr_result, measured_at=now)

            # 最后移动文件到 alpha_src
            prepare_for_archive(factor)
            self.to_lib(factor)

            q.put(("done", factor.name, "pass", "→ lib", "green"))
            return "pass"

        except CheckSkip as e:
            # stage 归因:CheckSkip/CheckFail 只可能从 checker.check() 抛出,
            # 此时 current_stage 恒为所在 stage(exception 自己不携带 stage,
            # 见 checker/base.py)。
            stage_name = current_stage or "archive"
            if current_stage:
                _emit_stage_done(current_stage, Status.SKIPPED)
            logger.warning("check skipped factor={} stage={} reason={}",
                           factor.key, stage_name, str(e))
            check.finished_at = now_iso()
            check.passed = None
            check.failed_stage = stage_name
            check.fail_reason = str(e)
            repo.append_check(factor.name, check)
            repo.transition(factor.name, FactorStatus.SUBMITTED)
            q.put(("done", factor.name, "error",
                   f"⊝ {stage_name} skipped: {str(e)[:60]}", "yellow"))
            return "error"

        except CheckFail as e:
            stage_name = current_stage or "archive"
            if stage_name in RETRYABLE_STAGES:
                if current_stage:
                    _emit_stage_done(current_stage, Status.RETRYABLE)
                logger.warning("check retryable failure factor={} stage={} reason={}",
                               factor.key, stage_name, str(e))
                # Environmental/config failure — revert to SUBMITTED, keep in staging
                check.finished_at = now_iso()
                check.passed = False
                check.failed_stage = stage_name
                check.fail_reason = str(e)
                repo.append_check(factor.name, check)
                repo.transition(factor.name, FactorStatus.SUBMITTED)
                q.put(("done", factor.name, "error",
                       f"↻ retry: {stage_name} ({str(e)[:60]})", "yellow"))
                return "error"
            else:
                if current_stage:
                    _emit_stage_done(current_stage, Status.FAILED)
                logger.warning("check rejected factor={} stage={} reason={}",
                               factor.key, stage_name, str(e))
                # Factor quality failure — REJECTED (src → alpha_src)
                now = now_iso()
                # 测得快照:correlation 失败自带测得值(CheckFail
                # .result = CorrResult);compliance 失败 long_backtest 已跑完,
                # 补一次 simsummary(轻)。必须在 on_reject 搬运前采集
                # (datasources 依赖 staging 里的 py_file)。快照失败不阻断拒绝。
                try:
                    _cr = getattr(e, "result", None)
                    _cr = _cr if isinstance(_cr, CorrResult) else None
                    _m = _cr.metrics if _cr else None
                    if _m is None and stage_name in KEEP_ARTIFACTS_STAGES \
                            and factor.pnl_file.exists():
                        _m = Runner.run_simsummary(factor.pnl_file, self.config)
                    if _m is not None:
                        self._persist_derived(factor, _m, _cr, measured_at=now)
                except Exception:
                    logger.exception("measured snapshot on reject failed factor={}",
                                     factor.name)
                self.on_reject(factor, stage_name)
                check.finished_at = now
                check.passed = False
                check.failed_stage = stage_name
                check.fail_reason = str(e)
                # 失败事实随 check 事件入 factor_history(读侧走 Factor.last_fail
                # 派生)
                repo.append_check(factor.name, check)
                repo.transition(factor.name, FactorStatus.REJECTED)
                q.put(("done", factor.name, "fail",
                       f"→ rejected/{stage_name}: {str(e)[:60]}", "red"))
                return "fail"

        except Exception as e:
            # Environment / framework bug — NOT a factor problem.
            # Keep factor in staging, leave meta.json untouched, revert state to SUBMITTED.
            if current_stage:
                _emit_stage_done(current_stage, Status.FAILED)
            logger.exception("check pipeline crashed factor={}", factor.key)
            check.finished_at = now_iso()
            check.passed = None
            check.fail_reason = f"unexpected: {e}"
            repo.append_check(factor.name, check)
            repo.transition(factor.name, FactorStatus.SUBMITTED)
            q.put(("done", factor.name, "error",
                   f"! unexpected: {str(e)[:80]}", "red"))
            return "error"

    def run(self):
        banner("因子检测")

        if not self.metadatas:
            info("没有待检测因子")
            bottom()
            return

        ctx = mp.get_context("fork")
        rows = make_factor_rows([f.name for f in self.metadatas], STAGES)

        # Stage events go through a Manager-backed Queue. Manager.Queue is a
        # proxy (~5ms per put/get) but is the only multiprocessing queue that
        # works reliably as a ProcessPoolExecutor task argument; raw mp.Queue
        # cannot survive being pickled into the pool's task pipe. The overhead
        # is negligible vs the 30+min wall time of long_backtest per factor.

        # Temporarily redirect loguru's stderr sink so any logger.warning /
        # logger.exception during the pool run renders above the Live region
        # instead of tearing the table mid-update. Restore on exit.
        stderr_redirected = False
        try:
            logger.remove(STDERR_SINK_ID)
            stderr_redirected = True
        except ValueError:
            # already removed elsewhere — fine
            pass

        try:
            with mp.Manager() as mgr, ProcessPoolExecutor(
                max_workers=min(20, max(1, len(self.metadatas))),
                mp_context=ctx,
            ) as pool:
                q = mgr.Queue()
                futures: list[Future[str]] = [
                    pool.submit(self.run_one, factor, i, q)
                    for i, factor in enumerate(self.metadatas)
                ]
                # During Live, route loguru WARNING+ to live.console.print so it
                # appears above the live region without corrupting it.
                live_sink_id = logger.add(
                    lambda msg: _printer_console.print(msg, end=""),
                    level="WARNING",
                    format="<level>{level: <8}</level> | <cyan>{name}:{function}:{line}</cyan> - {message}",
                    colorize=True,
                    backtrace=False,
                    diagnose=False,
                )
                try:
                    driver = LiveDriver(rows, q, futures, STAGES,
                                        console=_printer_console)
                    passed, failed, errored, locked = driver.run()
                finally:
                    try:
                        logger.remove(live_sink_id)
                    except ValueError:
                        pass
        finally:
            if stderr_redirected:
                # Re-add the stderr sink with the same config as ops/utils/log.py.
                # Keep this in sync with that module's STDERR_SINK_ID definition.
                import sys
                logger.add(
                    sys.stderr,
                    level="WARNING",
                    format="<level>{level: <8}</level> | <cyan>{name}:{function}:{line}</cyan> - {message}",
                    colorize=True,
                    backtrace=False,
                    diagnose=False,
                    enqueue=True,
                )

        banner("检测汇总")
        info(f"✔ 通过 : {passed:>4}")
        if failed > 0:
            error(f"✘ 未通过 : {failed:>4}")
        if errored > 0:
            warn(f"⚠ 异常 : {errored:>4}  (留在 staging,重跑 ops check 即可)")
        if locked > 0:
            warn(f"⚠ 占用 : {locked:>4}  (被其他进程持有,跳过)")

        report_path = write_check_report(
            self.config, self.config_path, rows,
            user=self._user, factor=self._factor,
        )
        info(f"报告 : {report_path}")
        if failed > 0 or errored > 0:
            info("完整失败原因见上述报告 / ~/.cache/ops/logs/ops.log")
        bottom()


def run_check(args):
    users: list[str] | None = [args.user] if args.user else None
    config_path: Path = args.config_path
    factor: str | None = args.factor_name

    pipeline = CheckerPipeline(
        users=users,
        config_path=config_path,
        factor=factor,
    )
    pipeline.run()

