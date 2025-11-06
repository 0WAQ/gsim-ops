import os
import glob
import subprocess
import shutil
from typing import Optional


def generate_setup_py(info: dict, compile_opt: str) -> bool:
    """
    动态生成 setup.py
    """

    setup_content = f'''
from setuptools import setup, Extension
from Cython.Build import cythonize

module_name = "{info['module_name']}"
sources = ["{info['source_file']}"]
# output_dir = "{info['folder_path']}"

extension = [
    Extension(
        name=module_name,
        sources=sources,
        extra_compile_args=["{compile_opt}"],
        language='c'
    )
]

setup(
    name=module_name,
    version="1.0",
    description="Auto-generated for GSIM Alpha factor",
    # build_lib=output_dir,
    ext_modules=cythonize(
        extension,
        compiler_directives={{"language_level": "3"}}
    )
)'''

    try:
        with open(f"{info['setup']}", "w+", encoding="utf-8") as f:
            f.write(setup_content.strip())
        return True
    except Exception as e:
        print(f"❌ 生成setup.py失败: {str(e)}")
        return False


def compile_alpha(info: dict, venv_path: str) -> Optional[str]:
    """
    编译 Python 源文件为 .so 共享库
    """

    try:
        compile_cmd = f"{venv_path}/bin/python {info['setup']} build_ext --build-lib {info['folder_path']} --inplace"
        result = subprocess.run(compile_cmd, shell=True, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            raise Exception(result.stderr[:200])
        
        # 查找生成的 .so 文件
        so_pattern = f"{info['module_name']}.cpython-310-x86_64-linux-gnu.so"
        so_files = glob.glob(so_pattern)
        # print(os.path.join(info['folder_path'], so_pattern))
        if os.path.exists(os.path.join(info['folder_path'], so_pattern)):
            os.remove(os.path.join(info['folder_path'], so_pattern))
        shutil.move(so_files[0], info['folder_path'])
        if not so_files:
            raise Exception("未找到生成的 .so 文件")
        
        return os.path.abspath(os.path.join(info['folder_path'], so_pattern))
    except Exception as e:
        print(f"❌ 编译失败：{str(e)}")
        return None
