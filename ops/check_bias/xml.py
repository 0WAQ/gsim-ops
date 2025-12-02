import os
import sys
import xmltodict
from glob import glob

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

    f = open(xml_path, 'r+', encoding="utf-8")

    root = xmltodict.parse(f.read())
    gsim = root["gsim"]

    gsim["Modules"]["Alpha"]["@module"] = py_path
    gsim["Constants"]["@niodatapath"] = "/datasvc/data/cc"
    gsim["Universe"]["@enddate"] = "20221230"
    gsim["Universe"]["@startdate"] = "20221201"
    gsim["Portfolio"]["Stats"]["@pnlDir"] = PNL_DIR
    gsim["Portfolio"]["Alpha"]["@dumpAlphaFile"] = "false"

    f.seek(0)
    f.write(xmltodict.unparse(root, pretty=True, full_document=False, encoding="utf-8"))
    f.truncate()
    return py_path, xml_path
