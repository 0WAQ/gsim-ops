import argparse

from ops.cli.common import add_config_arg, mark_write
from ops.services.produce import run_produce


def add_produce_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "produce",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="因子产线驱动:checkpoint 续跑日增(归档 XML 即生产态)",
        epilog="""\
Example:
    ops produce                      # sync + 全部 ACTIVE 续跑(T 日盘前跑)
    ops produce AlphaXxx -u lhw      # 定向(跳过停线对账)
    ops produce --dry-run            # 产线体检:XML 形态/checkpoint/dump 至,不跑
    ops produce --sync-only          # 只做产线同步(停线/新线报告)
    ops produce --force AlphaXxx -y  # 删 checkpoint 全段重跑(确认制)
    ops produce --enddate 20251231 AlphaXxx   # 钉死日重算(临时副本,不碰生产 checkpoint)
""",
    )

    parser.add_argument("factors", nargs="*", help="显式因子名(缺省 = 全部 ACTIVE)")
    parser.add_argument("--user", "-u", type=str, default=None, help="按作者过滤")
    parser.add_argument("--dry-run", action="store_true",
                        help="产线体检(XML 形态 / checkpoint / dump 进度),不跑 gsim")
    parser.add_argument("--sync-only", action="store_true",
                        help="只做产线同步(停线归 .retired + 新线报告),不跑 gsim")
    parser.add_argument("--force", action="store_true",
                        help="删 checkpoint 全段重跑(须显式点名因子,确认制)")
    parser.add_argument("--enddate", type=str, default=None,
                        help="钉死日重算 YYYYMMDD(临时 XML 副本 + 一次性 checkpoint)")
    parser.add_argument("--grouped", action="store_true",
                        help="分组模式:跑在产(组产 + 单产),而非逐因子"
                             "(设计 docs/design/factor-produce-groups.md)")
    parser.add_argument("--groups-only", action="store_true",
                        help="分组模式下只跑组产(不跑单产)")
    parser.add_argument("--single-only", nargs="*", metavar="FACTOR", default=None,
                        help="分组模式下只跑单产:无名字 = 全部注册单产;"
                             "点名 = 指定因子(pending 中的先准入再跑);"
                             "与 --groups-only 互斥")
    parser.add_argument("--timeout", type=int, default=None,
                        help="单次 gsim 运行超时秒数(缺省 config.mode.timeout=1800;"
                             "bootstrap 全史首跑需放大,如 43200)")
    parser.add_argument("-y", "--yes", action="store_true", help="跳过 --force 确认")
    parser.add_argument("--workers", "-w", type=int, default=8, help="并行进程数 (默认 8)")
    add_config_arg(parser)

    mark_write(parser)

    parser.set_defaults(func=run_produce)
