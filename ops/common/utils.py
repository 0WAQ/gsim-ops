import os
import sys


def check_path_exists(path: str, path_type: str = "file"):
    path = os.path.abspath(path)
    if not os.path.exists(path):
        sys.exit(f"路径不存在: {path}")
    # if path_type == "file" and not os.path.isfile(path):
    #     sys.exit(f"❌ 文件不存在：{path}")
    # else:
    #     sys.exit(f"❌ 目录不存在：{path}")


def ensure_dir_exists(path: str) -> None:
    os.makedirs(path, exist_ok=True)


