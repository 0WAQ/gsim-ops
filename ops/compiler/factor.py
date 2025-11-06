import os
import sys
import glob
from typing import Optional
from .env import check_compiler_env
from .compile import generate_setup_py, compile_alpha
from .xml import backup_xml, modify_xml_module
from .backtest import run_gsim
from .clean import clean_intermediate_files


def get_factor_info(date_dir_abs: str, folder: str) -> tuple[Optional[dict], str]:
    folder_path = os.path.join(date_dir_abs, folder)
    py_files = glob.glob(f"{folder_path}/*.py")
    xml_files = glob.glob(f"{folder_path}/*.xml")

    if len(py_files) != 1:
        return None, f".py文件数量异常 (找到{len(py_files)}个)"
    if len(xml_files) != 1:
        return None, f".xml文件数量异常 (找到{len(xml_files)}个)"

    # 解析 XML 获取模块名和源码路径
    try:
        from xml.etree import ElementTree as ET
        tree = ET.parse(xml_files[0])
        alpha_node = tree.find(".//Modules/Alpha")
        if alpha_node is None:
            return None, "XML 中为找到 Alpha 节点"
        
        module_name = alpha_node.get("id")
        source_file = alpha_node.get("module")
        if not module_name or not source_file:
            return None, "XML 缺少 id 或 module 属性"

        # 处理相对路径
        if not os.path.isabs(source_file):
            source_file = os.path.join(folder_path, source_file)
        if not os.path.exists(source_file):
            return None, f"源码文件不存在: {source_file}"

    except Exception as e:
        return None, f"XML 解析失败: {str(e)}"

    xml_file = os.path.join(os.path.curdir, xml_files[0])
    return {
        "module_name": module_name,
        "xml_file": xml_file,
        "source_file": source_file,
        "folder_path": folder_path,
        "setup": os.path.join(folder_path, "setup.py")
    }, ""


def process_single_factor(args, folder: str) -> bool:
    # 1. 提取因子信息
    info, err = get_factor_info(args.date_dir_abs, folder)
    if not info:
        print(f"❌ 因子信息提取失败: {err}")
        return False
    info['unix_id'] = args.unix_id

    # 2. 生成 setup.py
    if not generate_setup_py(info, args.compile_opt):
        return False
    
    # 3. 编译生成 .so
    so_abs_path = compile_alpha(info, args.venv_path)
    if not so_abs_path:
        return False
    print(f"✅ 编译成功：{os.path.basename(so_abs_path)}")

    # 4. XML 备份 + 修改
    if args.xml_backup:
        backup_xml(info["xml_file"])
    if not modify_xml_module(info["xml_file"], info["module_name"], so_abs_path):
        return False
    
    # 5. 可选: 回测
    if args.enable_backtest:
        if not run_gsim(args.venv_path, info["xml_file"]):
            return False
        
    # 6. 清理中间文件
    clean_intermediate_files(info)
    return True


def run_compiler(args):
    # 1. 环境校验
    check_compiler_env(args)
    success_count = 0
    fail_count = 0
    fail_folders = []

    # 2. 批量处理所有因子
    for folder in args.factor_folders:
        if process_single_factor(args, folder):
            success_count += 1
        else:
            fail_count += 1
            fail_folders.append(folder)

    # 3. 输出执行总结
    print("\n" + "="*50)
    print(f"Compiler 执行总结 (UnixId: {args.unix_id})")
    print(f"总因子数: {len(args.factor_folders)} | 成功: {success_count} | 失败: {fail_count}")
    if fail_folders:
        print(f"失败因子: {', '.join(fail_folders)}")
    print("="*50)
    return
