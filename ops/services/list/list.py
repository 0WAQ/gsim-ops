import json
from colorama import Fore, Style, init

from ops.core.library import LibraryScanner, FactorInfo
from .metrics import load_metrics, refresh_metrics, merge_metrics


init(autoreset=True)


def print_table(factors: list[FactorInfo]):
    if not factors:
        print(Fore.YELLOW + "No factors found.")
        return

    header = f"{'name':<40} {'author':<10} {'ret%':>8} {'shrp':>8} {'mdd%':>8} {'tvr%':>8} {'fitness':>8}"
    separator = "\u2500" * len(header)

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
        print(f"{f.name:<40} {f.author:<10} {ret} {shrp} {mdd} {tvr} {fitness}")

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


def run_list(args):
    scanner = LibraryScanner.from_config_path(args.config_path)
    factors = scanner.scan(refresh=args.refresh)

    if args.user:
        factors = scanner.filter_by_author(factors, args.user)

    if args.refresh_metrics:
        metrics = refresh_metrics(factors, scanner.config, args.config_path)
    else:
        metrics = load_metrics(args.config_path)

    factors = merge_metrics(factors, metrics)

    if args.sort and args.sort in SORT_KEYS:
        factors.sort(key=SORT_KEYS[args.sort], reverse=True)

    if args.n is not None:
        factors = factors[:args.n]

    if args.format == "json":
        print_json(factors)
    else:
        print_table(factors)
