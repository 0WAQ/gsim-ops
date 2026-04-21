from colorama import Fore, Style, init
from ops.core.library import LibraryScanner
from ops.services.list.metrics import load_metrics
from ops.services.list.datasource import load_datasources


init(autoreset=True)

def run_info(args):
    """Run the info command."""
    scanner = LibraryScanner.from_config_path(args.config_path)
    factor = scanner.get(args.factor_name)

    if factor is None:
        print(Fore.RED + f"Factor not found: {args.factor_name}")
        print(Fore.YELLOW + f"Check if the factor exists in: {scanner.alpha_src}")
        return

    # Get date range
    first_date, last_date = scanner.get_dump_date_range(factor.name)
    date_range = f"{first_date} ~ {last_date}" if first_date else "N/A"

    # Get metrics
    metrics_map = load_metrics(args.config_path)
    metrics = metrics_map.get(factor.name)

    # Print info
    separator = "─" * 60
    print(Fore.CYAN + separator)
    print(Fore.CYAN + Style.BRIGHT + f" Factor: {factor.name}")
    print(Fore.CYAN + separator)

    print(f"  {'Author:':<15} {factor.author}")
    print()
    print(Fore.YELLOW + "  Paths:")
    print(f"    {'Source:':<12} {factor.src_path}")
    print(f"    {'Dump:':<12} {factor.dump_path}")
    print(f"    {'PNL:':<12} {factor.pnl_path}")
    print()
    print(Fore.YELLOW + "  Statistics:")
    print(f"    {'Dump Days:':<12} {factor.dump_days}")
    print(f"    {'Date Range:':<12} {date_range}")
    print(f"    {'Has PNL:':<12} {Fore.GREEN + 'Yes' if factor.has_pnl else Fore.RED + 'No'}")
    print()
    print(Fore.YELLOW + "  Metrics:")
    if metrics:
        print(f"    {'ret%:':<12} {metrics.ret:.2f}")
        print(f"    {'shrp:':<12} {metrics.shrp:.2f}")
        print(f"    {'mdd%:':<12} {metrics.mdd:.2f}")
        print(f"    {'tvr%:':<12} {metrics.tvr:.2f}")
        print(f"    {'fitness:':<12} {metrics.fitness:.2f}")
    else:
        print(f"    {'—  (run ops list --refresh-metrics to fetch)'}")

    # Data Sources
    ds_map = load_datasources(args.config_path)
    ds = ds_map.get(factor.name)
    print()
    print(Fore.YELLOW + "  Data Sources:")
    if ds:
        print(f"    {'Tables:':<12} {', '.join(ds.get('tables', []))}")
        print(f"    {'Fields:':<12} {', '.join(ds.get('fields', []))}")
    else:
        print(f"    {'—  (run ops list --refresh-datasources to fetch)'}")

    print(Fore.CYAN + separator)
