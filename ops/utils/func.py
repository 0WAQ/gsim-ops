import mmap
import hashlib
from pathlib import Path
from datetime import datetime, timedelta


def debug(*args):
    print(args)
    while True:
        pass

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
    except:
        return None