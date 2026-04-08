"""List factors in the library."""

import json
from colorama import Fore, Style, init

from ..common.library import LibraryScanner, FactorInfo

init(autoreset=True)


def print_table(factors: list[FactorInfo]):
    """Print factors in table format."""
    if not factors:
        print(Fore.YELLOW + "No factors found.")
        return
    
    # Header
    header = f"{'Name':<40} {'Author':<10} {'Dump Days':>10} {'Has PNL':>8}"
    separator = "─" * 72
    
    print(Fore.CYAN + separator)
    print(Fore.CYAN + Style.BRIGHT + header)
    print(Fore.CYAN + separator)
    
    # Rows
    for f in factors:
        pnl_status = Fore.GREEN + "✓" if f.has_pnl else Fore.RED + "✗"
        print(f"{f.name:<40} {f.author:<10} {f.dump_days:>10} {pnl_status:>8}")
    
    print(Fore.CYAN + separator)
    print(f"Total: {len(factors)} factors")


def print_json(factors: list[FactorInfo]):
    """Print factors in JSON format."""
    data = [
        {
            "name": f.name,
            "author": f.author,
            "src_path": str(f.src_path),
            "dump_path": str(f.dump_path),
            "pnl_path": str(f.pnl_path),
            "has_pnl": f.has_pnl,
            "dump_days": f.dump_days,
        }
        for f in factors
    ]
    print(json.dumps(data, indent=2, ensure_ascii=False))


def run_list(args):
    """Run the list command."""
    scanner = LibraryScanner.from_config_path(args.config_path)
    factors = scanner.scan()
    
    # Filter by author if specified
    if args.user:
        factors = scanner.filter_by_author(factors, args.user)
    
    # Output
    if args.format == "json":
        print_json(factors)
    else:
        print_table(factors)
