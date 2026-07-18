#!/usr/bin/env python
"""接管闸门:combo 腿清单 × ops ACTIVE 差集核对(factor-produce-v3.md §5/§9 步骤 4)。

产线接管后生产集 = ops ACTIVE;combo 正在消费("腿")但不在 ACTIVE 的因子
会停产 —— 这些必须先裁决(补入库 or combo 摘腿),ops 不擅断。本脚本只读,
逐份 combo XML 抽出 AlphaLoad 腿(alphaDir 含 --alpha-dir 子串的),与 ACTIVE
集对差,任一 combo 有"腿不在 ACTIVE"即退出码 1(闸门不过)。

用法(170):
    uv run python scripts/audit_combo_legs.py /nvme125/combo/xml/*/mode0.xml
    # 无 PG 环境可用名单文件替代:
    uv run python scripts/audit_combo_legs.py --active-file active.txt <xml...>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ops.infra.config import Config, get_default_config_path    # noqa: E402
from ops.utils.xmlio import load_xml                            # noqa: E402


def combo_legs(xml_file: Path, alpha_dir_sub: str) -> list[str]:
    """抽出 AlphaLoad 腿的 @id(只认 alphaDir 含指定子串的 —— mode1/combo_eq
    读的是 combo_dump,不是因子腿,靠这个过滤排除)。"""
    legs: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            if (node.get("@module") == "AlphaLoad"
                    and alpha_dir_sub in str(node.get("@alphaDir", ""))):
                legs.append(str(node.get("@id")))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(load_xml(xml_file))
    return legs


def _active_names(args) -> set[str]:
    if args.active_file:
        return {ln.strip() for ln in
                Path(args.active_file).read_text().splitlines() if ln.strip()}
    from ops.infra.repository import FactorRepository
    config = Config.load(args.config_path)
    return {f.name for f in FactorRepository(config).find(status="active")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("xmls", nargs="+", type=Path, help="combo 生产 XML(mode0)")
    ap.add_argument("--config-path", "-c", type=Path,
                    default=get_default_config_path())
    ap.add_argument("--alpha-dir", default="alpha_dump",
                    help="因子腿的 alphaDir 识别子串(缺省 alpha_dump)")
    ap.add_argument("--active-file", default=None,
                    help="ACTIVE 名单文件(每行一名;替代 PG 查询)")
    args = ap.parse_args()

    active = _active_names(args)
    print(f"ACTIVE: {len(active)}")
    gate_ok = True
    for xml in args.xmls:
        legs = combo_legs(xml, args.alpha_dir)
        missing = sorted(set(legs) - active)
        print(f"\n{xml}: 腿 {len(legs)},不在 ACTIVE {len(missing)}")
        for name in missing:
            print(f"  ✘ {name}")
        if missing:
            gate_ok = False
    print("\n闸门:" + ("通过 —— 全部腿都在 ACTIVE" if gate_ok
                       else "不过 —— 上列因子接管后将停产,先裁决(补入库 or 摘腿)"))
    return 0 if gate_ok else 1


if __name__ == "__main__":
    sys.exit(main())
