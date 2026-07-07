"""时间戳单一真相源 (full-review S12 / craft B3)。

wire format 约定: **naive 本地时间 ISO 秒级字符串**。PG store 写入时打本地 tz
(_ts_in)、读出时剥离 (_ts_out);json 后端原样存。此前该表达式有 3 处 _now()
定义 + 10 处内联 + 1 次跨后端私有导入 —— info store 的 8h 时区偏移 bug 的根因
就是抄漏了配套约定。改格式必须全库一处改。
"""
from datetime import datetime


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
