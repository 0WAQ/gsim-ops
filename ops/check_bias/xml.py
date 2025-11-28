import os
import sys
from glob import glob
from xml.etree import ElementTree as ET
from xml.dom import minidom


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

    # 修改 Constants[niodatapath]
    constants_node = root.find("Constants")
    if constants_node is None:
        print("Not found node <Constants>")
        sys.exit(-1)
    if constants_node.get("niodatapath", None) is None:
        print("Not found attribute <Constants[niodatapath]>")
        sys.exit(-1)
    constants_node.set("niodatapath", "/datasvc/data/cc")

    # 修改 Universe[enddate]
    universe_node = root.find("Universe")
    if universe_node is None:
        print("Not found not <Universe>")
        sys.exit(-1)
    if universe_node.get("enddate", None) is None:
        print("Not found attribute <Universe[enddate]>")
        sys.exit(-1)
    universe_node.set("enddate", "20221231")

    if universe_node.get("startdate", None) is None:
        print("Not found attribute <Universe[startdate]>")
        sys.exit(-1)
    universe_node.set("startdate", "20221201")

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

    # 修改 Constants[niodatapath]
    constants_node.set("niodatapath", "/datasvc/data/cc0")

    pnl_path = os.path.join(PNL_DIR, alpha_id)
    pnl_cc0_path = pnl_path + ".cc0"

    xml_cc0_str = minidom.parseString(ET.tostring(root)).toprettyxml(indent="  ")
    xml_cc0_str = os.linesep.join([line for line in xml_cc0_str.splitlines() if line.strip()])
    with open(xml_cc0_path, "w", encoding="utf-8") as f:
        f.write(xml_cc0_str)

    return pnl_path, pnl_cc0_path, xml_path, xml_cc0_path
