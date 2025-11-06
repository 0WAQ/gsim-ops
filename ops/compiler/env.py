import os
import sys
import shutil
import subprocess
from ..common.utils import check_path_exists


def check_compiler_env(args) -> None:
    # 1.
    args.date_dir_abs = os.path.abspath(args.date_dir)
    check_path_exists(args.date_dir_abs, "dir")

    # 2.
    target_unix_id = args.unix_id.lower()

    all_folders = [f \
        for f in os.listdir(args.date_dir_abs) \
            if os.path.isdir(os.path.join(args.date_dir_abs, f))
    ]

    args.factor_folders = [f \
        for f in all_folders \
            if f.startswith("Alpha") # and (not args.filter_folder or args.filter_folder in f)
    ]

    if not args.factor_folders:
        sys.exit(f"❌ 未找到匹配UnixId[{target_unix_id}]的Alpha因子文件夹")

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
