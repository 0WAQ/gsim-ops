import os
import shutil


def clean_intermediate_files(info: dict) -> bool:
    try:
        # 1. 删除 setup.py
        if os.path.exists(info["setup"]):
            os.remove(info["setup"])

        # 2. 删除 c 源码
        c_file = f"{info['source_file'].rsplit('.', 1)[0]}.c"
        if os.path.exists(c_file):
            os.remove(c_file)

        # 3. 删除 build 目录
        build_dir = os.path.join(info["folder_path"], "build")
        # print("clean_intermediate_files: ", build_dir)
        if os.path.exists(build_dir):
            shutil.rmtree(build_dir)
        build_dir = os.path.join("/mnt/storage/dropbox", info['unix_id'], 'build')
        if os.path.exists(build_dir):
            shutil.rmtree(build_dir)

        # 4. 删除 Alpha 源文件
        # TODO:

        print("✅ 中间文件清理完成")
        return True
    except Exception as e:
        print(f"⚠️ 清理失败：{str(e)}")
        return False
