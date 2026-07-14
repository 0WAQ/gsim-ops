"""cli 层与下层(infra/core)的唯一合法接缝 —— import 契约的定点豁免模块。

契约禁止 cli 直接 import infra/core(否则各子命令各抄一份 `--config-path`
默认值 + FactorStatus choices,散成多份拷贝)。pyproject 的契约对本模块两条
import 定点 ignore,其余 cli 文件再碰 infra/core 即红。
"""
from pathlib import Path

from ops.core.metrics import SNAPSHOT_METRICS
from ops.core.state import FactorStatus
from ops.infra.config import Config, get_default_config_path

__all__ = ["FactorStatus", "METRIC_SORT_KEYS", "STATUS_CHOICES",
           "add_config_arg", "load_config", "mark_write"]

# argparse choices 用(list/pack/status 的 --status;restage 取枚举子集,
# 经本模块 re-export 的 FactorStatus,不直接碰 core)
STATUS_CHOICES = tuple(s.value for s in FactorStatus)

# list --sort-by 的 choices —— 从 metric 注册表派生(SSOT,core/metrics.py),
# 别在 cli 侧另抄一份键列表(注册表外的拷贝会漂)。
METRIC_SORT_KEYS = tuple(SNAPSHOT_METRICS)


def load_config(config_path) -> Config:
    """cli 层加载 Config 的唯一通道(cli 不直碰 infra;setup 的渲染在 cli 层,
    故它在 cli 侧需要 Config)。"""
    return Config.load(config_path)


def add_config_arg(parser) -> None:
    """统一的 `--config-path/-c`(否则各子命令各内联一份)。"""
    parser.add_argument("--config-path", "-c", type=Path,
                        default=get_default_config_path())


def mark_write(parser) -> None:
    """声明该子命令会写共享盘(alpha_src/staging/alpha_pnl/...)。

    sudo self-elevate(infra/sudo.py)据此派生提权名单 —— 写命令集是声明派生,
    不手抄:别退回 `WRITE_COMMANDS` 手抄集合(多真相源,新增写命令漏声明 =
    JFS 下非 root 直接 EACCES)。漏声明的新写命令会在金丝雀环路第一步暴露。
    """
    parser.set_defaults(is_write_command=True)
