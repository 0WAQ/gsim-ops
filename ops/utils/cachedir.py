"""ops 本机缓存根目录(~/.cache/ops)。

单独成模块的原因(import-linter C1/C5,factor-aggregate-plan 阶段 1):
CACHE_ROOT 同时被 `utils.log`(日志目录)与 `infra.cache`(state/lock 缓存布局)
使用。它原先住在 infra.cache,导致 utils→infra 的反向依赖 —— utils 是叶子层,
不得向上引用。路径约定本身不含 I/O 策略,是 utils 级常量,故正主落此;
infra.cache 从这里取(向下依赖,合法)。
"""
from pathlib import Path

CACHE_ROOT = Path.home() / ".cache" / "ops"
