"""进程 fd 限额自举。

gsim 按数据集逐文件 memmap,全史回测轻松打满 sudo 缺省的 soft ulimit 1024
(170 金丝雀实测)。170 的产线 crontab 各行手写 `ulimit -n 65535` 就是同一
问题的分散解 —— ops 自提权(sudo)后环境自带,不该指望调用方记得抬限额,
入口统一自举,produce/check/run 所有出 gsim 的路径一次覆盖。
"""
import resource

# gsim 全史回测的经验安全线(170 产线 crontab 同值)
MIN_NOFILE = 65536


def raise_nofile(min_soft: int = MIN_NOFILE) -> None:
    """把 RLIMIT_NOFILE 软限额抬到 min(min_soft, 硬限额);已够高则不动。
    失败只静默放过 —— 限额抬不上去时让 gsim 的 EMFILE 自己响亮,别让
    自举本身炸掉 `ops --help`。"""
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        target = min_soft if hard == resource.RLIM_INFINITY else min(min_soft, hard)
        if target > soft:
            resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    except (ValueError, OSError):
        pass
