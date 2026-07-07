"""gsim XML 配置读写的唯一入口。

写格式统一为 `xmltodict.unparse(pretty=True, encoding="utf-8", full_document=False)`:
此前 7 处调用点(check / restage / run / submit.normalize / checkbias_checker /
xml_prepare)各手抄一份 unparse 参数,任何一处漏 `full_document=False` 就会给
XML 加 `<?xml?>` 声明头,格式静默漂移 —— 属 full-review S 组"同一事实多处手抄"。
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
