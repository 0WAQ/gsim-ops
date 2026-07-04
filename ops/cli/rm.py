import argparse
from pathlib import Path

from ops.infra.config import get_default_config_path
from ops.services.rm import run_rm


def add_rm_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "rm",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="彻底删除因子(src/pnl/dump/feature + state + derived,不可逆)",
        epilog="""\
Example:
    ops rm AlphaWbaiFoo                # 彻底删除,交互确认
    ops rm AlphaWbaiFoo -y             # 跳过确认

删除因子的全部落点:alpha_src / alpha_pnl / alpha_dump / alpha_feature +
factor_state 行 + factor_derived 行。**不可逆**,恢复只能重新 ops submit。
没有软删/墓碑。
""",
    )

    parser.add_argument("factor_name", type=str, help="Factor name to delete")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="Skip confirmation prompt")
    parser.add_argument("--config-path", "-c", type=Path,
                        default=get_default_config_path())

    parser.set_defaults(func=run_rm)
