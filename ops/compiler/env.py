import os
import sys
import shutil
import subprocess
from ..common.utils import check_path_exists


def check_compiler_env(args) -> None:
    # 1. 校验
    check_path_exists(args.dropbox_user_date_path, "dir")

    # 2. 寻找因子路径
    alpha_abs_pahts: list[str] = []
    for entry in os.listdir(str(args.dropbox_user_date_path)):
        # TODO: startswith Alpha{Unix_id}
        alpha_abs_path: str = os.path.join(args.dropbox_user_date_path, entry)
        if entry.startswith("Alpha") and os.path.isdir(alpha_abs_path):
            alpha_abs_pahts.append(alpha_abs_path)

    if len(alpha_abs_pahts) == 0:
        sys.exit(f"❌ 未找到匹配UnixId[{args.unix_id}]的Alpha因子文件夹")
    args.alpha_abs_paths = alpha_abs_pahts

    # 3. 校验全局虚拟环境
    venv_activate = os.path.join(args.venv_path, "bin", "activate")
    if not os.path.exists(venv_activate):
        print(f"Error: Can't find venv path: {args.venv_path}.")
        sys.exit(1)

    # 4. 校验依赖工具
    if shutil.which("gcc") is None:
        print(f"Error: Can't find C compiler (gcc).")
        sys.exit(1)

    if shutil.which("uv") is None:
        print(f"Error: Can't find uv tools.")
        sys.exit(1)
