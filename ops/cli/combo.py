import argparse
from pathlib import Path

from ops.cli.common import add_config_arg
from ops.services.combo import run_combo


def add_combo_subparser(subparser: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparser.add_parser(
        "combo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="combo 端到端代测(predict + backtest)",
        epilog="""\
Example:
    # 模型型 (有 predict/): 留 warmup, 只跑 simple
    ops combo run path/to/CombolhwEqualV23 --start 20241210 --end 20241231 \\
        --predict-start 20241201 --stats simple
    # 纯线性 (无 predict/): 直接回测全段
    ops combo run path/to/ComboLinear --start 20240102 --end 20241231
""",
    )
    sub = parser.add_subparsers(title="combo command", dest="combo_cmd", required=True)

    run_p = sub.add_parser("run", help="端到端跑一个 combo (predict + backtest)")
    run_p.add_argument("combo_dir", type=Path, help="combo 目录 (任意路径)")
    run_p.add_argument("--start", required=True, help="回测起点 yyyymmdd")
    run_p.add_argument("--end", required=True, help="回测终点 yyyymmdd")
    run_p.add_argument("--predict-start", default=None,
                       help="predict 起点 (留 warmup, 默认 = --start)")
    run_p.add_argument("--data-root", default=None,
                       help="cc 数据根 (默认取 config 的 nio_data_path = cc_2025)")
    run_p.add_argument("--stats", default="simple,bench,layer,opt",
                       help="跑哪些 stats, 逗号分隔 (默认全部)")
    run_p.add_argument("--device", default="cpu", help="predict 设备 (默认 cpu)")
    add_config_arg(run_p)
    run_p.set_defaults(func=run_combo)
