"""操作者身份 —— factor_history.actor 的唯一取值口(schema v2b)。

ops 的写命令经 infra/sudo.py self-elevate 以 root 跑,getpass 会答 "root" ——
真实操作者在 SUDO_USER 里。顺序:SUDO_USER > 系统用户。
"""
import getpass
import os


def current_actor() -> str:
    try:
        return os.environ.get("SUDO_USER") or getpass.getuser()
    except Exception:  # getuser 在无 passwd 条目的容器里可能抛
        return "unknown"
