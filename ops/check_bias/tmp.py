#!/usr/bin/env python3

import os
import sys
import shutil
import subprocess as sp
from typing import Optional
from glob import glob
from xml.etree import ElementTree as ET
from xml.dom import minidom

def run_diff(lhs: str, rhs: str, out: str) -> Optional[str]:
    try:
        output_path = os.path.join(os.path.dirname(lhs), "diff.txt")
        with open(output_path, 'w+') as f:
            _ = sp.run(["diff", lhs, rhs], stdout=f, text=True)
            size = f.seek(0, )
            if size != 0:
                with open(out, 'w+') as f1:
                    f1.write(os.path.dirname(lhs))
                    f1.writelines(f.readlines())
                print("Error: forward looking!")    # TODO: 
        print("run diff succeed")
        return output_path
    except sp.CalledProcessError as e:
        print(f"run diff failed: {e}")


def run_backtest(xml_path: str):
    try:
        python = "/usr/local/gsim/.venv/bin/python"
        run_py = "/usr/local/gsim/run.py"
        sp.run([python, run_py, xml_path], stdout=sp.PIPE, text=True)
        print("✅ backtest succeed")
    except sp.CalledProcessError as e:
        print(f"❌ backtest failed: {e}")


def run_simsummary(pnl_path: str) -> Optional[str]:
    try:
        python = "/usr/local/gsim/.venv/bin/python"
        simsummary_py = "/usr/local/gsim/tools/simsummary.py"
        sim_path = pnl_path + ".sim"
        with open(sim_path, 'w+') as f:
            sp.run([python, simsummary_py, pnl_path], stdout=f, text=True)
        print("✅ simsummary succeed")
        return sim_path
    except Exception as e:
        print(f"❌ simsummary failed: {e}")
        return None


def do_xml(alpha_path: str) -> tuple[str, ...]:
    PNL_DIR = alpha_path

    py_path: str | None = None
    xml_path: str | None = None

    # 寻找 .py 和 .xml
    _tmp = glob(os.path.join(alpha_path, "*.py"))
    if len(_tmp) != 0:
        py_path = _tmp[0]            

    _tmp = glob(os.path.join(alpha_path, "*.xml"))
    if len(_tmp) != 0:            
        xml_path = _tmp[0]

    if xml_path is None or py_path is None:
        print("Not found xml or py file")
        sys.exit(-1)
    print(f"Found {py_path}")  # TODO: log
    print(f"Found {xml_path}") # TODO: log

    xml_cc0_path = xml_path + ".cc0"

    # TODO: 使用 minidom
    root = ET.parse(xml_path).getroot()

    # 修改 Modules/Alpha/module
    alpha_node = root.find("Modules/Alpha") # TODO: 还应该要用 id 来定位, 可能有多个 Alpha
    if alpha_node is None:
        print("Not found node <Modules/Alpha>")
        sys.exit(-1)
    if alpha_node.get("module", None) is None:
        print("Not found attribute <Modules/Alpha[module]>")
        sys.exit(-1)
    alpha_node.set("module", py_path)

    # 定位 Portfolio
    portfolio_node = root.find("Portfolio")
    if portfolio_node is None:
        print("Not found node <Portfolio>")
        sys.exit(-1)

    # 修改 Portfolio/Stats/pnlDir
    stats_node = portfolio_node.find("Stats")
    if stats_node is None:
        print("Not found node <Stats>")
        sys.exit(-1)
    if stats_node.get("pnlDir", None) is None:
        print("Not found attribute <Stats[pnlDir]>")
        sys.exit(-1)
    stats_node.set("pnlDir", PNL_DIR)

    # 修改 Portfolio/Alpha/dumpAlphaFile
    alpha_node = portfolio_node.find("Alpha")    # TODO: 还应该要用 id 来定位, 可能有多个 Alpha
    if alpha_node is None:
        print("Not found node <Portfolio/Alpha>")
        sys.exit(-1)
    if alpha_node.get("dumpAlphaFile", None) is None:
        print("Not found attribute <Portfolio/Alpha[dumpAlphaFile]>")
    else:
        alpha_node.set("dumpAlphaFile", "false")

    xml_str = minidom.parseString(ET.tostring(root)).toprettyxml(indent="  ")
    xml_str = os.linesep.join([line for line in xml_str.splitlines() if line.strip()])
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write(xml_str)

    alpha_id = alpha_node.get("id")
    if alpha_id is None:
        print("Not found attribute <Portfolio/Alpha[id]>]")
        sys.exit(-1)

    # 修改 Alpha[id], 生成的 pnl 文件名称
    alpha_node.set("id", alpha_id + '.cc0')

    pnl_path = os.path.join(PNL_DIR, alpha_id)
    pnl_cc0_path = pnl_path + ".cc0"

    # 修改 Constants[niodatapath]
    constants_node = root.find("Constants")
    if constants_node is None:
        print("Not found node <Constants>")
        sys.exit(-1)
    constants_node.set("niodatapath", "/datasvc/data/cc0")


    xml_cc0_str = minidom.parseString(ET.tostring(root)).toprettyxml(indent="  ")
    xml_cc0_str = os.linesep.join([line for line in xml_cc0_str.splitlines() if line.strip()])
    
    with open(xml_cc0_path, "w", encoding="utf-8") as f:
        f.write(xml_cc0_str)

    return pnl_path, pnl_cc0_path, xml_path, xml_cc0_path


def check_bias():
    src = "/mnt/storage/dropbox"
    dst = "/tmp/check_bias"

    user = "fguo"
    dates = ["20251121", "20251126"]

    if os.path.exists(dst):
        shutil.rmtree("/tmp/check_bias")

    if not os.path.exists(dst):
        os.makedirs(dst)

    user_src = os.path.join(src, user)
    if not os.path.exists(user_src):
        os.makedirs(user_src)

    user_dst = os.path.join(dst, user)
    for date in dates:
        user_date_src = os.path.join(user_src, date)
        user_date_dst = os.path.join(user_dst, date)
        if not os.path.exists(user_date_src):
            print(f"{user_date_src} doesn't exist.")
            continue

        print(user_date_src, user_date_dst)
        if not os.path.exists(user_date_dst):
            shutil.copytree(user_date_src, user_date_dst)


    users = [user]
    # 遍历 users
    for user in users:
        user_path = os.path.join(dst, user)
        if not os.path.exists(user_path):
            continue

        # 遍历 dates
        for date in dates:
            user_date_path = os.path.join(user_path, date)
            if not os.path.exists(user_date_path):
                continue

            # 遍历 Alpha
            for alpha in os.listdir(user_date_path):
                alpha_path = os.path.join(user_date_path, alpha)

                # 修改 xml
                pnl_path, pnl_cc0_path, xml_path, xml_cc0_path = do_xml(alpha_path)
                print(pnl_path)
                print(pnl_cc0_path)
                print(xml_path)
                print(xml_cc0_path)

                # 回测 cc       
                print("backtest cc")
                run_backtest(xml_path)
                cc = run_simsummary(pnl_path)

                # 回测 cc0
                print("backtest cc0")
                run_backtest(xml_cc0_path)
                cc0 = run_simsummary(pnl_cc0_path)

                if cc is None or cc0 is None:
                    print(f"{cc} or {cc0} is None")
                    sys.exit(0)
                print("run diff")

                # diff
                output = run_diff(cc, cc0, "/tmp/result")
                if output is None:
                    sys.exit(0)


check_bias()
