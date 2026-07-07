from concurrent.futures import Future, ProcessPoolExecutor, as_completed
from pathlib import Path

import xmltodict

from ops.infra.config import Config
from ops.infra.gsim.runner import BacktestError, Runner
from ops.infra.lock import FactorLocked, factor_lock
from ops.utils.log import logger
from ops.utils.printer import banner, bottom, error, info, progress, warn

from .find import scan_factors


def _override_dates(xml_file: Path, start_date: str, end_date: str) -> tuple[str, str]:
    """Override XML Universe startdate/enddate. Returns original values for restore."""
    cfg = xmltodict.parse(xml_file.read_text(encoding="utf-8"))
    universe = cfg["gsim"]["Universe"]
    orig_start = universe.get("@startdate", "")
    orig_end = universe.get("@enddate", "")
    universe["@startdate"] = start_date
    universe["@enddate"] = end_date
    xml_file.write_text(
        xmltodict.unparse(cfg, pretty=True, encoding="utf-8", full_document=False),
        encoding="utf-8",
    )
    return orig_start, orig_end


def _restore_dates(xml_file: Path, orig_start: str, orig_end: str) -> None:
    cfg = xmltodict.parse(xml_file.read_text(encoding="utf-8"))
    universe = cfg["gsim"]["Universe"]
    universe["@startdate"] = orig_start
    universe["@enddate"] = orig_end
    try:
        xml_file.write_text(
            xmltodict.unparse(cfg, pretty=True, encoding="utf-8", full_document=False),
            encoding="utf-8",
        )
    except Exception:
        logger.exception("XML restore failed file={} orig=({},{}) — XML may be in bad state",
                         xml_file, orig_start, orig_end)


def run_one(factor_dir: Path, config: Config,
            start_date: str, end_date: str,
            i: int, total: int) -> str:
    """Returns one of: 'pass' | 'fail' | 'locked'."""
    name = factor_dir.name
    total_n = len(str(total))
    prog = (i + 1) / total
    bar = f"[{i+1:>{total_n}}/{total}] {prog:>6.1%}"
    progress(f"{bar} running ", name)

    try:
        with factor_lock(name, config):
            return _run_one_locked(factor_dir, config, start_date, end_date)
    except FactorLocked:
        warn(f"  ⚠  {name} 已被另一个进程占用,跳过")
        return "locked"


def _run_one_locked(factor_dir: Path, config: Config,
                    start_date: str, end_date: str) -> str:
    name = factor_dir.name

    # Load metadata from the directory (AlphaMetadata constructor needs user/date,
    # but those are only used to build AlphaKey — we derive them from factor_dir)
    # AlphaMetadata expects user, date, factor_dir, config — but user/date are
    # just for the AlphaKey display key. For run, we use placeholder values since
    # the key is only printed/logged.
    # However, AlphaMetadata requires the dir to exist and contain .xml/.py.
    # We bypass AlphaMetadata's constructor and directly use the files.

    xml_files = list(factor_dir.glob("*.xml"))
    py_files = list(factor_dir.glob("*.py"))
    if not xml_files or not py_files:
        error(f"  ✘  {name} 缺少 xml 或 py 文件")
        return "fail"

    xml_file = xml_files[0]

    # Override dates
    orig_start, orig_end = _override_dates(xml_file, start_date, end_date)

    try:
        # Run backtest
        Runner.run_backtest(xml_file, config)
        info(f"  ✔  {name} backtest passed")

        # Run simsummary
        pnl_file = config.alpha_pnl / name
        if pnl_file.exists():
            metrics = Runner.run_simsummary(pnl_file, config)
            if metrics:
                info(f"  📊 {name} ret={metrics.ret:.2f}% shrp={metrics.shrp:.2f}")

        return "pass"

    except BacktestError as e:
        error(f"  ✘  {name} backtest failed: {e}")
        return "fail"
    except Exception as e:
        error(f"  ✘  {name} error: {e}")
        return "fail"
    finally:
        _restore_dates(xml_file, orig_start, orig_end)


def run_factors(args) -> None:
    config = Config.load(args.config_path)
    config.alpha_src.mkdir(exist_ok=True)
    config.alpha_dump.mkdir(exist_ok=True)
    config.alpha_pnl.mkdir(exist_ok=True)

    users: list[str] | None = [args.user] if hasattr(args, 'user') and args.user else None
    factor_name: str | None = getattr(args, 'factor_name', None)

    factors = scan_factors(users, factor_name, config)

    if not factors:
        warn("No factors found.")
        return

    start_date: str = args.start_date
    end_date: str = args.end_date
    banner("因子运行")

    passed = failed = locked = 0
    total = len(factors)
    with ProcessPoolExecutor(max_workers=min(20, max(1, total))) as pool:
        futures: list[Future[str]] = []
        for i, (factor_dir, _) in enumerate(factors):
            f = pool.submit(run_one, factor_dir, config, start_date, end_date, i, total)
            futures.append(f)

        for f in as_completed(futures):
            match f.result():
                case "pass":
                    passed += 1
                case "fail":
                    failed += 1
                case "locked":
                    locked += 1

    banner("运行汇总")
    info(f"✔ 通过 : {passed:>4}")
    if failed > 0:
        error(f"✘ 失败 : {failed:>4}")
    if locked > 0:
        warn(f"⚠ 占用 : {locked:>4}")
    bottom()
