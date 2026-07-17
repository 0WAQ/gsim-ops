"""produce 工作区 XML 构造(字段权威对照 services/check/xml_prepare.py)。

只作用于工作区**副本**,永不碰 alpha_src 原件:归档 XML 被 check 的
prepare_for_archive 拆雷(输出指 /tmp/alphalib)、窗口残留 long_backtest、
Data 项 niodatapath 是 check 时代写进去的 cc_2025 —— 全部要在副本上改;
原地改-还原(ops run 的模式)有崩溃残留脏生产库的竞态。
"""
from __future__ import annotations

import re
from pathlib import Path

from ops.utils.xmlio import load_xml, save_xml

# cc 数据根的路径形态:/datasvc/data/<视图>/<尾巴>。换根 = 换 <视图> 段。
# 不复用 AlphaMetadata._update_data_niodatapath —— 它只认 /datasvc/data/cc/
# 前缀(submit 进来的 QR 原始 XML),对归档 XML 的 cc_2025 前缀是 no-op。
_DATA_ROOT_RE = re.compile(r"^/datasvc/data/[^/]+(?=/|$)")


def rebase_niodatapath(old: str, new_root: str) -> str | None:
    """`/datasvc/data/cc_2025/xxx` → `<new_root>/xxx`;非 cc 形态返回 None(不动)。"""
    if not _DATA_ROOT_RE.match(old):
        return None
    return _DATA_ROOT_RE.sub(new_root.rstrip("/"), old)


def prepare_produce_xml(workdir: Path, *, start: int, end: int, nio_root: Path,
                        dump_root: Path, pnl_dir: Path,
                        checkpoint_dir: Path) -> Path:
    """改写工作区 XML 为日增生产形态,返回 XML 路径。

    - Universe 窗口 = 缺失段 [start, end](普通因子无 warmup 问题:gsim 在
      generate(di) 内部读 cc 历史,区别于 combo 预 predict npy 的起点边界);
    - 数据根整体换到 produce 根(Constants + 各 Data 项同步,否则 Constants
      看得见 2026 而 Data 项还锁在 cc_2025,静默读旧);
    - dump 开、pnl 关:日增只产 dump,不碰 alpha_pnl(pnlDir 防御性指工作区,
      即使 gsim 无视 dumpPnl 也落不到生产);
    - checkpointDir 指工作区(调用方保证已清空重建 —— 陈旧 checkpoint 会被
      gsim load 直接崩)。
    """
    xmls = sorted(workdir.glob("*.xml"))
    if not xmls:
        raise FileNotFoundError(f"工作区无 XML: {workdir}")
    xml_file = xmls[0]
    cfg = load_xml(xml_file)
    gsim = cfg["gsim"]

    gsim["Universe"]["@startdate"] = str(start)
    gsim["Universe"]["@enddate"] = str(end)

    gsim["Constants"]["@niodatapath"] = str(nio_root)
    gsim["Constants"]["@checkpointDir"] = str(checkpoint_dir) + "/"

    data_items = gsim.get("Modules", {}).get("Data", [])
    if isinstance(data_items, dict):
        data_items = [data_items]
    for item in data_items:
        old = item.get("@niodatapath")
        if old:
            rebased = rebase_niodatapath(old, str(nio_root))
            if rebased is not None:
                item["@niodatapath"] = rebased

    gsim["Portfolio"]["Alpha"]["@dumpAlphaFile"] = "true"
    gsim["Portfolio"]["Alpha"]["@dumpAlphaDir"] = str(dump_root)
    gsim["Portfolio"]["Stats"]["@dumpPnl"] = "false"
    gsim["Portfolio"]["Stats"]["@pnlDir"] = str(pnl_dir)

    save_xml(xml_file, cfg)
    return xml_file
