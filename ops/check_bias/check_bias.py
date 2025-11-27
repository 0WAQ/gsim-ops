import os
import sys
import shutil
from glob import glob
from xml.etree import ElementTree as ET
from xml.dom import minidom
from datetime import datetime, timedelta

from ..common.utils import Local, Gsim


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

def run_check_bias(args):
    src = args.dropbox_path
    dst = args.target_path

    # TODO: 
    if os.path.exists(dst):
        shutil.rmtree("/tmp/check_bias")

    if not os.path.exists(dst):
        os.makedirs(dst)

    user_src = os.path.join(src, args.unix_id)
    args.user_src = user_src

    # 拷贝到临时目录
    user_dst = os.path.join(dst, args.unix_id)
    args.user_dst = user_dst

    start_date = datetime.strptime(args.start_date, "%Y%m%d")
    end_date = datetime.strptime(args.end_date, "%Y%m%d") if args.end_date is not None else None

    dates = []
    if end_date is None:
        cur_date = args.start_date
        cur_path = os.path.join(user_src, cur_date)
        if not Local.check_is_dir(cur_path):
            print(f"WARN: {cur_path} doesn't exist.")
            return  # TODO: return

        dates.append(cur_date)
    else:
        for t in range(int((end_date - start_date).days) + 1):
            cur_date = (start_date + timedelta(1) * t).strftime("%Y%m%d")
            cur_path = os.path.join(user_src, cur_date)
            if not Local.check_is_dir(cur_path):
                print(f"WARN: {cur_path} doesn't exist.")
                continue

            dates.append(cur_date)

    args.dates = dates
    for date in dates:
        user_date_src = os.path.join(user_src, date)
        user_date_dst = os.path.join(user_dst, date)
        if not os.path.exists(user_date_src):
            print(f"{user_date_src} doesn't exist.")
            continue

        print(user_date_src, user_date_dst)
        if not os.path.exists(user_date_dst):
            shutil.copytree(user_date_src, user_date_dst)

    check_bias(args)


def check_bias(args):
    dst = args.target_path

    user = args.unix_id # TODO: to list?
    dates = args.dates

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
                Gsim.run_backtest(xml_path)
                cc = Gsim.run_simsummary(pnl_path)

                # 回测 cc0
                print("backtest cc0")
                Gsim.run_backtest(xml_cc0_path)
                cc0 = Gsim.run_simsummary(pnl_cc0_path)

                if cc is None or cc0 is None:
                    print(f"{cc} or {cc0} is None")
                    sys.exit(0)
                print("run diff")

                # diff
                output = Gsim.run_diff(cc, cc0, "/tmp/result")
                if output is None:
                    sys.exit(0)

