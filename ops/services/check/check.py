import multiprocessing as mp
import shutil
import xmltodict
from datetime import datetime
from pathlib import Path
from concurrent.futures import Future, ProcessPoolExecutor, as_completed

from ops.infra.config import Config
from ops.infra.gsim.runner import Runner
from ops.infra.store import default_store
from ops.infra.info import default_info_store, FactorInfo
from ops.infra.snapshot import default_snapshot_store, FactorSnapshot
from ops.infra.lock import factor_lock, FactorLocked
from ops.services.list.datasource import (
    parse_datasources,
    resolve_tables,
    _build_npy_index,
)
from ops.utils.printer import *
from ops.utils.printer import _console as _printer_console
from ops.utils.log import logger, STDERR_SINK_ID
from ops.core.alpha.metadata import AlphaMetadata
from ops.core.factormeta import FactorMeta
from ops.core.state import FactorRecord, FactorStatus, CheckRecord
from ops.core.alpha.results.compliance import *
from ops.core.alpha.results.correlation import *
from ops.core.alpha.results.checkpoint import *

# Imported AFTER the `results.*` star imports so its Status doesn't get
# shadowed by the stub Status enum in core/alpha/results/base.py.
from ops.utils.live_table import LiveDriver, Status, make_factor_rows

from .xml_prepare import *
from .report import write_check_report
from .checker.base import *
from .checker.validate_checker import ValidateChecker
from .checker.checkbias_checker import CheckbiasChecker
from .checker.checkpoint_checker import CheckpointChecker
from .checker.long_backtest_checker import LongBacktestChecker
from .checker.compliance_checker import ComplianceChecker
from .checker.correlation_checker import CorrelationChecker

# Stages whose failure is likely environmental/config — revert to SUBMITTED, leave in staging.
_RETRYABLE_STAGES = {"validate", "long_backtest"}

# Stage names + display order for the Live table. Must match keys used in
# _run_one_locked when emitting (stage_start, ...) / (stage_done, ...) events.
STAGES = ("validate", "checkbias", "checkpoint", "long_backtest", "compliance", "correlation")


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
        # in tests. Unset (production) → construct the real gsim-backed checkers,
        # behavior unchanged.
        checkers = checkers or {}
        self.validate_checker = checkers.get("validate") or ValidateChecker(config=self.config)
        self.checkbias_checker = checkers.get("checkbias") or CheckbiasChecker(config=self.config)
        self.checkpoint_checker = checkers.get("checkpoint") or CheckpointChecker(config=self.config)
        self.long_backtest_checker = checkers.get("long_backtest") or LongBacktestChecker(config=self.config)
        self.compliance_checker = checkers.get("compliance") or ComplianceChecker(config=self.config)
        self.correlation_checker = checkers.get("correlation") or CorrelationChecker(config=self.config)


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

    def _persist_derived(self, factor: AlphaMetadata, metrics, corr_result) -> None:
        """入库前把所有派生数据写入 factor_snapshot (入库时快照，不可变)。

        2026-07-06 重构: 从四次 upsert (index/metrics/datasources/bcorr) 改为一次性
        写入 factor_snapshot。snapshot_at = factor_state.entered_at (入库时间)。

        必须在 to_lib 之前调 —— datasources 依赖 factor.py_file(此时仍在 staging)。
        各组独立 try，互不阻断入库（快照缺失可运维补救，但入库不能失败）。

        delay 从 XML 解析定死 (factor.delay)，与 metrics 同性质不可变。原 index 组的
        has_pnl/dump_days 是可变物理事实，已从快照删除 (需实时状态走 LibraryScanner)。
        """
        snapshot_store = default_snapshot_store(self.config)
        state_store = default_store(self.config)

        # 获取入库时间（factor_state.entered_at）
        state = state_store.get(factor.name)
        if not state or not state.entered_at:
            logger.error("Cannot persist snapshot: factor {} has no entered_at", factor.name)
            return

        snapshot_at = state.entered_at

        # 准备各组数据
        ret = shrp = mdd = tvr = fitness = None
        if metrics:
            ret, shrp, mdd, tvr, fitness = (
                metrics.ret, metrics.shrp, metrics.mdd, metrics.tvr, metrics.fitness
            )

        fields = tables = None
        try:
            fields = parse_datasources(factor.py_file)
            tables = resolve_tables(fields, _build_npy_index(self.config.nio_data_path))
        except Exception:
            logger.exception("parse datasources failed factor={}", factor.name)

        max_bcorr = max_bcorr_factor = None
        if corr_result is not None:
            max_bcorr = corr_result.max_bcorr
            max_bcorr_factor = corr_result.max_bcorr_factor

        # 一次性写入 snapshot
        try:
            # Re-archive 自愈:restage / submit --overwrite 在离库时删旧快照,但
            # 迁移期存量 REJECTED 快照行、或删除步骤崩掉的残留仍可能在。快照语义
            # = "本次入库事件的快照",旧行必须让位 —— 否则 insert 撞 name UNIQUE
            # 被吞,反查/报告永远读到上一版代码的指标(full-review P0-1)。
            if snapshot_store.get(factor.name) is not None:
                logger.warning("stale snapshot exists, replacing factor={}", factor.name)
                snapshot_store.delete(factor.name)
            snapshot_store.insert(FactorSnapshot(
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
                snapshot_at=snapshot_at,
            ))
        except Exception:
            logger.exception("persist snapshot failed factor={}", factor.name)

    def to_lib(self, factor: AlphaMetadata):
        self._clean_pycache(factor.dir)

        src_dst = self.config.alpha_src / factor.dir.name
        if src_dst.exists():
            shutil.rmtree(src_dst)
        shutil.move(factor.dir, self.config.alpha_src)
        self._rewrite_module_path(src_dst)

        dump_dst = self.config.alpha_dump / factor.alpha_dir.name
        if dump_dst.exists():
            shutil.rmtree(dump_dst)
        shutil.move(factor.alpha_dir, self.config.alpha_dump)

        # alpha_pnl/<name> 是单文件(根 CLAUDE.md 明文警告的 Errno 20 反模式):
        # restage 保留 pnl → re-archive 时此处必有旧文件,rmtree 对文件抛
        # NotADirectoryError(full-review 第一部分 1.2)。目录形态只可能是远古残留。
        pnl_dst = self.config.alpha_pnl / factor.name
        if pnl_dst.is_dir():
            shutil.rmtree(pnl_dst)
        elif pnl_dst.exists():
            pnl_dst.unlink()
        shutil.move(factor.pnl_file, pnl_dst)

        # 按因子来源 (discovery_method) 把 pnl 额外分流一份到 pnl_automated / pnl_manual。
        # factor.pnl_file 此时已被 move 走,从入库后的 pnl_dst 拷。pnl 是单文件,copy2。
        bucket = {"automated": self.config.pnl_automated,
                  "manual": self.config.pnl_manual}.get(factor.discovery_method)
        if bucket is not None:
            bucket.mkdir(parents=True, exist_ok=True)
            shutil.copy2(pnl_dst, bucket / factor.name)
        else:
            logger.warning("discovery_method 缺失/非法, 跳过 pnl 分流 factor={} value={}",
                           factor.name, factor.discovery_method)

    def on_reject(self, factor: AlphaMetadata, e: CheckFail):
        """因子质量失败:src 归档到 alpha_src(与 ACTIVE 同库,状态由 state 区分),
        按失败阶段决定产物保留,最后清掉 staging 原物。不再写 recycle 目录。
        """
        self._clean_pycache(factor.dir)

        # alpha_src: 从 staging 复制一份(REJECTED 与 ACTIVE 的 src 都在 alpha_src,
        # 状态靠 state 区分,不靠目录位置)
        src_dst = self.config.alpha_src / factor.name
        if src_dst.exists():
            shutil.rmtree(src_dst)
        shutil.copytree(factor.dir, src_dst)
        self._rewrite_module_path(src_dst)

        # 按失败阶段区分产物保留策略:
        # - compliance/correlation 失败: 保留 pnl + dump,生成 feature
        # - checkbias/checkpoint 失败: 不保留 pnl/dump/feature(数据不完整)
        _LATE_STAGES = {"compliance", "correlation"}

        if e.stage in _LATE_STAGES:
            # 保留 pnl
            if factor.pnl_file.exists():
                shutil.copy2(factor.pnl_file, self.config.alpha_pnl / factor.name)
            # 保留 dump
            dump_src = self.config.alpha_dump / factor.alpha_dir.name
            if factor.alpha_dir.exists() and not dump_src.exists():
                shutil.move(str(factor.alpha_dir), str(dump_src))
            # 生成 feature
            # from ops.services.pack.pack import pack_one, load_universe, PACK_L
            # try:
            #     nio = load_universe(self.config.nio_data_path)
            #     _, instruments, date_to_idx = nio
            #     shape = (PACK_L, len(instruments))
            #     pack_one(factor.name, self.config.alpha_dump,
            #              self.config.alpha_feature, date_to_idx, shape)
            # except Exception:
            #     pass
        else:
            # checkbias/checkpoint: 清掉 dump + feature(短期数据不完整)
            dump_dir = self.config.alpha_dump / factor.name
            if dump_dir.exists():
                shutil.rmtree(dump_dir)
            for v in ("v1", "v2"):
                f = self.config.alpha_feature / f"{factor.name}.{v}.npy"
                if f.exists():
                    f.unlink()

        # 清掉 staging 原物(src 已进 alpha_src,不再需要归档副本)
        shutil.rmtree(factor.dir, ignore_errors=True)

    def _ensure_record(self, factor: AlphaMetadata, store) -> None:
        if store.get(factor.name) is not None:
            return
        # state record 缺失（crash 恢复 / 直接 check staging 未经 submit）时补建。
        # 三表结构：author/discovery_method 写 factor_info，状态机字段写 factor_state。
        meta_path = factor.dir / "meta.json"
        author = factor.key.user
        discovery_method = factor.discovery_method
        submitted_at = datetime.now().isoformat(timespec="seconds")
        if meta_path.exists():
            try:
                meta = FactorMeta.load(meta_path)
                author = meta.author or author
                discovery_method = meta.discovery_method or discovery_method
                submitted_at = meta.submitted_at or submitted_at
            except Exception:
                pass
        now = datetime.now().isoformat(timespec="seconds")
        info_store = default_info_store(self.config)
        info_store.upsert(FactorInfo(
            name=factor.name,
            author=author,
            discovery_method=discovery_method,
            created_at=submitted_at,
        ))
        store.put(FactorRecord(
            name=factor.name,
            status=FactorStatus.SUBMITTED,
            version=1,
            updated_at=now,
            submitted_at=submitted_at,
        ))

    def run_one(self, factor: AlphaMetadata, i: int, q) -> str:
        """Returns one of: 'pass' | 'fail' | 'error' | 'locked'.

        Stage events are emitted via the queue `q` so the parent's LiveDriver
        can render them. The return string is also still consumed by the
        parent for counter accumulation.
        """
        try:
            with factor_lock(factor.name, self.config):
                return self._run_one_locked(factor, q)
        except FactorLocked:
            q.put(("done", factor.name, "locked", "🔒 已被另一个进程占用", "yellow"))
            return "locked"

    def _run_one_locked(self, factor: AlphaMetadata, q) -> str:
        store = default_store(self.config)
        self._ensure_record(factor, store)
        check = CheckRecord(started_at=datetime.now().isoformat(timespec="seconds"))
        store.transition(factor.name, FactorStatus.CHECKING)

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
            # 0. Validate — short backtest, no firewall (env/config check)
            _emit_stage_start("validate")
            prepare_for_validate(factor)
            self.validate_checker.check(factor)
            _emit_stage_done("validate", Status.PASSED)

            # 1. Checkbias — firewall injection + short backtest
            _emit_stage_start("checkbias")
            prepare_for_checkbias(factor)
            self.checkbias_checker.check(factor)
            _emit_stage_done("checkbias", Status.PASSED)

            # 2. Checkpoint — breakpoint stability
            _emit_stage_start("checkpoint")
            prepare_for_checkpoint(factor)
            self.checkpoint_checker.check(factor)
            self.checkpoint_checker.clean(factor)
            _emit_stage_done("checkpoint", Status.PASSED)

            # 3. Long Backtest — full history (pure run, no checks)
            _emit_stage_start("long_backtest")
            prepare_for_long_backtest(factor)
            self.long_backtest_checker.check(factor)
            _emit_stage_done("long_backtest", Status.PASSED)

            # 4. Compliance — position limits check
            _emit_stage_start("compliance")
            prepare_for_compliance(factor)
            self.compliance_checker.check(factor)
            _emit_stage_done("compliance", Status.PASSED)

            # 5. Correlation — correlation against library
            _emit_stage_start("correlation")
            prepare_for_correlation(factor)
            corr_result = self.correlation_checker.check(factor)
            _emit_stage_done("correlation", Status.PASSED)

            # 6. Archive — simsummary + mark ACTIVE (设置 entered_at) + persist snapshot + move to lib
            #    (no live column; folded into outcome)
            metrics = Runner.run_simsummary(factor.pnl_file, self.config)

            # 先设置 entered_at (入库时间)，snapshot_at 依赖这个时间戳
            now = datetime.now().isoformat(timespec="seconds")
            check.finished_at = now
            check.passed = True
            store.append_check(factor.name, check)
            store.transition(factor.name, FactorStatus.ACTIVE, entered_at=now)

            # 再写入 snapshot (需要 entered_at 作为 snapshot_at)
            self._persist_derived(factor, metrics, corr_result)

            # 最后移动文件到 alpha_src
            prepare_for_archive(factor)
            self.to_lib(factor)

            q.put(("done", factor.name, "pass", "→ lib", "green"))
            return "pass"

        except CheckSkip as e:
            if current_stage:
                _emit_stage_done(current_stage, Status.SKIPPED)
            logger.warning("check skipped factor={} stage={} reason={}",
                           factor.key, e.stage, str(e))
            check.finished_at = datetime.now().isoformat(timespec="seconds")
            check.passed = None
            check.failed_stage = e.stage
            check.fail_reason = str(e)
            store.append_check(factor.name, check)
            store.transition(factor.name, FactorStatus.SUBMITTED)
            q.put(("done", factor.name, "error",
                   f"⊝ {e.stage} skipped: {str(e)[:60]}", "yellow"))
            return "error"

        except CheckFail as e:
            if e.stage in _RETRYABLE_STAGES:
                if current_stage:
                    _emit_stage_done(current_stage, Status.RETRYABLE)
                logger.warning("check retryable failure factor={} stage={} reason={}",
                               factor.key, e.stage, str(e))
                # Environmental/config failure — revert to SUBMITTED, keep in staging
                check.finished_at = datetime.now().isoformat(timespec="seconds")
                check.passed = False
                check.failed_stage = e.stage
                check.fail_reason = str(e)
                store.append_check(factor.name, check)
                store.transition(factor.name, FactorStatus.SUBMITTED)
                q.put(("done", factor.name, "error",
                       f"↻ retry: {e.stage} ({str(e)[:60]})", "yellow"))
                return "error"
            else:
                if current_stage:
                    _emit_stage_done(current_stage, Status.FAILED)
                logger.warning("check rejected factor={} stage={} reason={}",
                               factor.key, e.stage, str(e))
                # Factor quality failure — REJECTED (src → alpha_src)
                self.on_reject(factor, e)
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
                q.put(("done", factor.name, "fail",
                       f"→ rejected/{e.stage}: {str(e)[:60]}", "red"))
                return "fail"

        except Exception as e:
            # Environment / framework bug — NOT a factor problem.
            # Keep factor in staging, leave meta.json untouched, revert state to SUBMITTED.
            if current_stage:
                _emit_stage_done(current_stage, Status.FAILED)
            logger.exception("check pipeline crashed factor={}", factor.key)
            check.finished_at = datetime.now().isoformat(timespec="seconds")
            check.passed = None
            check.fail_reason = f"unexpected: {e}"
            store.append_check(factor.name, check)
            store.transition(factor.name, FactorStatus.SUBMITTED)
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

