from dataclasses import dataclass
from collections import Counter
from colorama import Fore, Style, init

from ops.core.library import LibraryScanner, FactorInfo
from ops.core.state import FactorStatus
from ops.infra.store import default_store
from ops.services.list.metrics import load_metrics, refresh_metrics
from ops.services.list.datasource import load_datasources, refresh_datasources


init(autoreset=True)

OK = "OK"
WARNING = "WARNING"
ERROR = "ERROR"

_LEVEL_COLOR = {OK: Fore.GREEN, WARNING: Fore.YELLOW, ERROR: Fore.RED}


@dataclass
class Issue:
    level: str
    category: str
    factor: str | None
    message: str


def _check_orphans(
    src_factors: list[FactorInfo], dump_dir, pnl_dir
) -> list[Issue]:
    src_names = {f.name for f in src_factors}
    issues: list[Issue] = []

    if dump_dir.exists():
        for d in dump_dir.iterdir():
            if d.is_dir() and d.name not in src_names:
                issues.append(Issue(WARNING, "orphan-dump", d.name, f"dump exists but no source: {d}"))

    if pnl_dir.exists():
        for d in pnl_dir.iterdir():
            if d.is_dir() and d.name not in src_names:
                issues.append(Issue(WARNING, "orphan-pnl", d.name, f"pnl exists but no source: {d}"))

    return issues


def _check_missing_dump(factors: list[FactorInfo]) -> list[Issue]:
    return [
        Issue(WARNING, "missing-dump", f.name, "source exists but dump is empty")
        for f in factors if f.dump_days == 0
    ]


def _check_missing_pnl(factors: list[FactorInfo]) -> list[Issue]:
    return [
        Issue(WARNING, "missing-pnl", f.name, f"no pnl at {f.pnl_path}")
        for f in factors if not f.has_pnl
    ]


def _check_missing_metrics(
    factors: list[FactorInfo], metrics: dict
) -> list[Issue]:
    return [
        Issue(WARNING, "missing-metrics", f.name, "has pnl but no cached metrics")
        for f in factors if f.has_pnl and f.name not in metrics
    ]


def _check_missing_datasources(
    factors: list[FactorInfo], datasources: dict
) -> list[Issue]:
    return [
        Issue(WARNING, "missing-datasources", f.name, "no cached datasources")
        for f in factors if f.name not in datasources
    ]


def _check_unresolved_tables(datasources: dict) -> list[Issue]:
    issues: list[Issue] = []
    for name, ds in datasources.items():
        fields = ds.get("fields", [])
        tables = ds.get("tables", [])
        if fields and not tables:
            issues.append(Issue(WARNING, "unresolved-tables", name,
                                f"{len(fields)} fields parsed but 0 tables resolved"))
    return issues


def _print_issues(issues: list[Issue]) -> None:
    if not issues:
        print(Fore.GREEN + "  (none)")
        return
    for i in issues:
        color = _LEVEL_COLOR.get(i.level, "")
        prefix = f"{color}{i.level:<8}{Style.RESET_ALL} {Style.DIM}{i.category:<20}{Style.RESET_ALL}"
        target = i.factor if i.factor else "-"
        print(f"  {prefix} {target:<45} {i.message}")


def run_health(args):
    scanner = LibraryScanner.from_config_path(args.config_path)
    factors = scanner.scan(refresh=args.refresh)

    statuses = {r.name: r.status for r in default_store(scanner.config).list()}
    factors = [f for f in factors if statuses.get(f.name) != FactorStatus.DELETED]

    if args.user:
        factors = scanner.filter_by_author(factors, args.user)

    metrics = load_metrics(args.config_path)
    datasources = load_datasources(args.config_path)

    issues: list[Issue] = []
    if not args.user:
        issues += _check_orphans(factors, scanner.alpha_dump, scanner.alpha_pnl)
    issues += _check_missing_dump(factors)
    issues += _check_missing_pnl(factors)
    issues += _check_missing_metrics(factors, metrics)
    issues += _check_missing_datasources(factors, datasources)
    issues += _check_unresolved_tables({f.name: datasources[f.name] for f in factors if f.name in datasources})

    fixed_msgs: list[str] = []
    if args.fix:
        need_metrics = any(i.category == "missing-metrics" for i in issues)
        need_ds = any(i.category == "missing-datasources" for i in issues)
        if need_metrics:
            print(Fore.CYAN + "Refreshing metrics...")
            metrics = refresh_metrics(factors, scanner.config, args.config_path)
            fixed_msgs.append("metrics refreshed")
        if need_ds:
            print(Fore.CYAN + "Refreshing datasources...")
            datasources = refresh_datasources(factors, scanner.config, args.config_path)
            fixed_msgs.append("datasources refreshed")
        if need_metrics or need_ds:
            issues = [i for i in issues if i.category not in ("missing-metrics", "missing-datasources")]
            issues += _check_missing_metrics(factors, metrics)
            issues += _check_missing_datasources(factors, datasources)

    separator = "─" * 70
    print(Fore.CYAN + separator)
    print(Fore.CYAN + Style.BRIGHT + f" Factor Library Health Check  ({len(factors)} factors{', user=' + args.user if args.user else ''})")
    print(Fore.CYAN + separator)
    _print_issues(issues)
    print(Fore.CYAN + separator)

    counts = Counter(i.level for i in issues)
    summary = " | ".join(
        f"{_LEVEL_COLOR.get(lvl, '')}{counts.get(lvl, 0)} {lvl}{Style.RESET_ALL}"
        for lvl in (ERROR, WARNING)
    )
    ok_status = Fore.GREEN + "ALL OK" + Style.RESET_ALL if not issues else summary
    print(f"Summary: {ok_status}")
    if fixed_msgs:
        print(Fore.CYAN + "Fixed: " + ", ".join(fixed_msgs))
