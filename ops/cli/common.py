"""cli 层与下层(infra/core)的唯一合法接缝 —— C2 契约的定点豁免模块。

C2(cli 不得直接 import infra/core)原有 18 条违例边:14 个子命令各抄一份
`--config-path` 默认值(get_default_config_path)+ 4 个子命令抄 FactorStatus
choices。2026-07-09 阶段 3 全部收敛到本模块,C2 转 enforcing —— pyproject 的
契约对本模块两条 import 定点 ignore,其余 cli 文件再碰 infra/core 即红。
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

# list --sort-by 的 choices —— 从 metric 注册表派生(SSOT S8,core/metrics.py)。
# 原先 cli/list.py 手抄一份键列表,是注册表外的第三份拷贝。
METRIC_SORT_KEYS = tuple(SNAPSHOT_METRICS)


def load_config(config_path) -> Config:
    """cli 层加载 Config 的唯一通道(C2:cli 不直碰 infra;setup 的渲染在
    cli 层 —— 展示层上收示范件 —— 故它在 cli 侧需要 Config)。"""
    return Config.load(config_path)


def add_config_arg(parser) -> None:
    """统一的 `--config-path/-c`(原 14 个子命令各内联一份)。"""
    parser.add_argument("--config-path", "-c", type=Path,
                        default=get_default_config_path())


def mark_write(parser) -> None:
    """声明该子命令会写共享盘(alpha_src/staging/alpha_pnl/...)。

    sudo self-elevate(infra/sudo.py)据此派生提权名单 —— S16:原
    `WRITE_COMMANDS` 手抄集合是多真相源,新增写命令漏改名单 = JFS 下非 root
    直接 EACCES(`run` 曾因此缺席,full-review 第一部分 1.2)。现在写性随
    子命令注册声明,漏声明的新写命令会在金丝雀环路第一步暴露。
    """
    parser.set_defaults(is_write_command=True)
