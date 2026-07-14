"""时间戳单一真相源。

wire format 约定: **naive 本地时间 ISO 秒级字符串**。PG store 写入时打本地 tz
(_ts_in)、读出时剥离 (_ts_out);json 后端原样存。别在别处内联复制这个表达式:
漏抄配套的 tz 约定会引入时区偏移。改格式必须全库一处改。
"""
from datetime import datetime


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
