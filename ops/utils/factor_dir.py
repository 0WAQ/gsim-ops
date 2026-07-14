"""因子目录搬迁的固定两件套:__pycache__ 清理 + XML @module 重写。

check.to_lib / check.on_reject / restage 把因子目录搬到新位置后都要做同样
两件事,集中于此,别在各调用方各抄一份。
"""
import shutil
from pathlib import Path

from ops.utils.xmlio import load_xml, save_xml


def clean_pycache(root: Path) -> None:
    for p in root.rglob("__pycache__"):
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)


def rewrite_module_path(d: Path) -> None:
    """把 XML 的 Modules.Alpha.@module 指向目录内的 .py,使因子搬家后仍可独立运行。"""
    xmls = list(d.glob("*.xml"))
    pys = list(d.glob("*.py"))
    if not xmls or not pys:
        return
    cfg = load_xml(xmls[0])
    modules_alpha = cfg.get("gsim", {}).get("Modules", {}).get("Alpha")
    if isinstance(modules_alpha, dict):
        modules_alpha["@module"] = str(pys[0])
        save_xml(xmls[0], cfg)
