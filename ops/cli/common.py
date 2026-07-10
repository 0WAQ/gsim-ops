"""cli 层与下层(infra/core)的唯一合法接缝 —— C2 契约的定点豁免模块。

C2(cli 不得直接 import infra/core)原有 18 条违例边:14 个子命令各抄一份
`--config-path` 默认值(get_default_config_path)+ 4 个子命令抄 FactorStatus
choices。2026-07-09 阶段 3 全部收敛到本模块,C2 转 enforcing —— pyproject 的
契约对本模块两条 import 定点 ignore,其余 cli 文件再碰 infra/core 即红。
"""
from pathlib import Path

from ops.core.state import FactorStatus
from ops.infra.config import get_default_config_path

__all__ = ["FactorStatus", "STATUS_CHOICES", "add_config_arg"]

# argparse choices 用(list/pack/status 的 --status;restage 取枚举子集,
# 经本模块 re-export 的 FactorStatus,不直接碰 core)
STATUS_CHOICES = tuple(s.value for s in FactorStatus)


def add_config_arg(parser) -> None:
    """统一的 `--config-path/-c`(原 14 个子命令各内联一份)。"""
    parser.add_argument("--config-path", "-c", type=Path,
                        default=get_default_config_path())
