"""ops 本机缓存根目录(~/.cache/ops)。

单独成模块的原因:CACHE_ROOT 同时被 `utils.log`(日志目录)与 `infra.cache`
(state/lock 缓存布局)使用。若它住在 infra.cache,utils.log→infra 就是反向
依赖 —— utils 是叶子层,不得向上引用。路径约定本身不含 I/O 策略,是 utils 级
常量,故正主落此;infra.cache 从这里取(向下依赖,合法)。
"""
from pathlib import Path

CACHE_ROOT = Path.home() / ".cache" / "ops"
