import argparse
from pathlib import Path

from ops.utils.utils import LowerAction
from ops.infra.config import get_default_config_path
from ops.services.check import run_check


def add_check_subparser(subparser: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparser.add_parser(
        "check",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Example:
    ops check                       # check all factors in staging/
    ops check -u wbai               # filter by submitted_by
    ops check -f AlphaWbaiReversal  # check one factor by name
""",
    )

    parser.add_argument("--user", "-u", default=None, type=str, action=LowerAction, help="filter by submitted_by")
    parser.add_argument("--factor-name", "-f", type=str, default=None, help="check one factor by name")
    # --retry 已删除:解析后从未被读取(no-op)。retry 语义早已由自动路由取代 ——
    # validate/long_backtest 失败自动回 SUBMITTED 留在 staging,下次 ops check
    # 无条件重扫(full-review 第三部分 V 表)。
    parser.add_argument("--config-path", "-c", type=Path, default=get_default_config_path())

    parser.set_defaults(func=run_check)
