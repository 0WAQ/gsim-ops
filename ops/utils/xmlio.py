"""gsim XML 配置读写的唯一入口。

写格式统一为 `xmltodict.unparse(pretty=True, encoding="utf-8", full_document=False)`:
别在调用点各手抄一份 unparse 参数 —— 漏 `full_document=False` 就会给 XML 加
`<?xml?>` 声明头,格式静默漂移。
"""
from pathlib import Path

import xmltodict


def load_xml(xml_file: Path) -> dict:
    return xmltodict.parse(xml_file.read_text(encoding="utf-8"))


def save_xml(xml_file: Path, cfg: dict) -> None:
    xml_file.write_text(
        xmltodict.unparse(cfg, pretty=True, encoding="utf-8", full_document=False),
        encoding="utf-8",
    )
