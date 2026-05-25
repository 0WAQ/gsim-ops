import json
import re
import fnmatch
from colorama import Fore, Style, init

from ops.core.library import LibraryScanner, FactorInfo
from ops.core.state import FactorStatus
from ops.infra.store import default_store
from .metrics import load_metrics, refresh_metrics, merge_metrics
from .datasource import load_datasources, refresh_datasources, merge_datasources


init(autoreset=True)


_STATUS_COLOR = {
    FactorStatus.ACTIVE:    Fore.GREEN,
    FactorStatus.REJECTED:  Fore.RED,
    FactorStatus.SUBMITTED: Fore.YELLOW,
    FactorStatus.CHECKING:  Fore.YELLOW,
    FactorStatus.DECAYING:  Fore.MAGENTA,
    FactorStatus.RETIRED:   Style.DIM,
    FactorStatus.DELETED:   Style.DIM,
}

def print_table(factors: list[FactorInfo], statuses: dict[str, FactorStatus],
                show_tables=False, show_fields=False):
    if not factors:
        print(Fore.YELLOW + "No factors found.")
        return

    header = f"{'name':<40} {'author':<10} {'ret%':>8} {'shrp':>8} {'mdd%':>8} {'tvr%':>8} {'fitness':>8}"
    if show_tables:
        header += f"  {'tables'}"
    if show_fields:
        header += f"  {'fields'}"
    separator = "\u2500" * max(len(header), 90)

    print(Fore.CYAN + separator)
    print(Fore.CYAN + Style.BRIGHT + header)
    print(Fore.CYAN + separator)

    for f in factors:
        m = f.metrics
        ret = f"{m.ret:>8.2f}" if m else f"{'—':>8}"
        shrp = f"{m.shrp:>8.2f}" if m else f"{'—':>8}"
        mdd = f"{m.mdd:>8.2f}" if m else f"{'—':>8}"
        tvr = f"{m.tvr:>8.2f}" if m else f"{'—':>8}"
        fitness = f"{m.fitness:>8.2f}" if m else f"{'—':>8}"
        line = f"{f.name:<40} {f.author:<10} {ret} {shrp} {mdd} {tvr} {fitness}"
        if show_tables and f.datasources:
            line += f"  {', '.join(f.datasources.get('tables', []))}"
        if show_fields and f.datasources:
            line += f"  {', '.join(f.datasources.get('fields', []))}"
        color = _STATUS_COLOR.get(statuses.get(f.name), "") # type: ignore
        print(color + line)

    print(Fore.CYAN + separator)
    print(f"Total: {len(factors)} factors")


def print_json(factors: list[FactorInfo]):
    data = [f.to_dict() for f in factors]
    print(json.dumps(data, indent=2, ensure_ascii=False))


SORT_KEYS = {
    "ret": lambda f: f.metrics.ret if f.metrics else float("-inf"),
    "shrp": lambda f: f.metrics.shrp if f.metrics else float("-inf"),
    "mdd": lambda f: f.metrics.mdd if f.metrics else float("-inf"),
    "tvr": lambda f: f.metrics.tvr if f.metrics else float("-inf"),
    "fitness": lambda f: f.metrics.fitness if f.metrics else float("-inf"),
    "dump_days": lambda f: f.dump_days,
}

METRIC_GETTERS = {
    "ret": lambda f: f.metrics.ret if f.metrics else None,
    "shrp": lambda f: f.metrics.shrp if f.metrics else None,
    "mdd": lambda f: f.metrics.mdd if f.metrics else None,
    "tvr": lambda f: f.metrics.tvr if f.metrics else None,
    "fitness": lambda f: f.metrics.fitness if f.metrics else None,
    "dump_days": lambda f: float(f.dump_days),
}

_FILTER_PATTERN = re.compile(r"^(\w+)([><=!]+)(.+)$")
FILTER_KEYS = {"tables", "field"} | set(METRIC_GETTERS.keys())


def parse_filters(filter_str: str) -> list[tuple[str, str, str]] | None:
    filters = []
    has_error = False
    for part in filter_str.split(","):
        part = part.strip()
        if not part:
            continue
        m = _FILTER_PATTERN.match(part)
        if m:
            key, op, value = m.group(1), m.group(2), m.group(3)
            if key not in FILTER_KEYS:
                print(f"Unknown filter key: '{key}'. Supported: {', '.join(sorted(FILTER_KEYS))}")
                has_error = True
                continue
            filters.append((key, op, value))
        else:
            print(f"Invalid filter syntax: '{part}'. Expected: key=value or key>value (use quotes: --filter-by \"...\")")
            has_error = True
    if has_error:
        return None
    return filters


def apply_filters(factors: list[FactorInfo], filters: list[tuple[str, str, str]]) -> list[FactorInfo]:
    result = factors
    for key, op, value in filters:
        if key == "tables":
            result = [
                f for f in result
                if f.datasources and any(fnmatch.fnmatch(t, value) for t in f.datasources.get("tables", []))
            ]
        elif key == "field":
            result = [f for f in result if f.datasources and value in f.datasources.get("fields", [])]
        elif key in METRIC_GETTERS:
            threshold = float(value)
            getter = METRIC_GETTERS[key]
            if op == ">":
                result = [f for f in result if (v := getter(f)) is not None and v > threshold]
            elif op == ">=":
                result = [f for f in result if (v := getter(f)) is not None and v >= threshold]
            elif op == "<":
                result = [f for f in result if (v := getter(f)) is not None and v < threshold]
            elif op == "<=":
                result = [f for f in result if (v := getter(f)) is not None and v <= threshold]
            elif op == "=":
                result = [f for f in result if (v := getter(f)) is not None and v == threshold]
    return result


def run_list(args):
    scanner = LibraryScanner.from_config_path(args.config_path)
    factors = scanner.scan(refresh=args.refresh)
    statuses = {r.name: r.status for r in default_store().list()}

    if args.user:
        factors = scanner.filter_by_author(factors, args.user)

    if args.status:
        factors = [f for f in factors if statuses.get(f.name) == args.status]
    else:
        factors = [f for f in factors if statuses.get(f.name) != FactorStatus.DELETED]

    if args.refresh_metrics:
        metrics = refresh_metrics(factors, scanner.config, args.config_path)
    else:
        metrics = load_metrics(args.config_path)

    factors = merge_metrics(factors, metrics)

    if args.refresh_datasources:
        datasources = refresh_datasources(factors, scanner.config, args.config_path)
    else:
        datasources = load_datasources(args.config_path)

    factors = merge_datasources(factors, datasources)

    if args.filter_by is not None:
        if not args.filter_by.strip():
            print("Empty filter expression.")
            return
        filters = parse_filters(args.filter_by)
        if filters is None:
            return
        factors = apply_filters(factors, filters)

    if args.sort_by and args.sort_by in SORT_KEYS:
        factors.sort(key=SORT_KEYS[args.sort_by], reverse=True)

    if args.n is not None:
        factors = factors[:args.n]

    if args.format == "json":
        print_json(factors)
    else:
        print_table(factors, statuses,
                    show_tables=args.show_tables, show_fields=args.show_fields)
