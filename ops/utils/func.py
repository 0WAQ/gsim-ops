import hashlib
import mmap
from datetime import datetime, timedelta
from pathlib import Path


def date_range(start: str, end: str):
    d =  datetime.strptime(start, "%Y%m%d").date()
    stop = datetime.strptime(end, "%Y%m%d").date()
    while d <= stop:
        yield d.strftime("%Y%m%d")
        d += timedelta(days=1)

def md5sum(file_path: str | Path) -> str | None:
    try:
        with open(file_path, "rb") as f, \
            mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            return hashlib.md5(mm).hexdigest()
    except (OSError, ValueError):
        # ValueError: mmap of a zero-length file. 窄捕获别退回裸 except
        # (会吞 KeyboardInterrupt);None 由调用方 (checkpoint_checker)
        # 按 md5 缺失处理。
        return None