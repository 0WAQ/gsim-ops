"""一次性: meta.json.delay 用 XML <Alpha @delay> 重写 (修 desc.delay stale 导致的 delay0 bug).

跑法: sudo /home/wbai/.local/share/uv/tools/ops/bin/python scripts/fix_delay_from_xml.py [--apply]

不带 --apply 是 dry-run, 只打印将要改的因子。
"""
import argparse
import json
import sys
from pathlib import Path

import xmltodict

ROOTS = [
    Path("/tank/vault/alphalib.local/staging"),
    Path("/tank/vault/alphalib.local/alpha_src"),
    Path("/tank/vault/alphalib.local/recycle"),
    Path("/mnt/storage/alphalib/staging"),
    Path("/mnt/storage/alphalib/alpha_src"),
    Path("/mnt/storage/alphalib/recycle"),
]


def alpha_delay_from_xml(factor_dir: Path) -> int | None:
    xmls = list(factor_dir.glob("*.xml"))
    if not xmls:
        return None
    try:
        x = xmltodict.parse(xmls[0].read_text(encoding="utf-8"))
        portfolio = x.get("gsim", {}).get("Portfolio", {})
        alpha = portfolio.get("Alpha", {})
        if isinstance(alpha, list):
            alpha = alpha[0]
        d = alpha.get("@delay")
        return int(d) if d is not None else None
    except Exception as e:
        print(f"  ! xml parse fail {factor_dir.name}: {e}", file=sys.stderr)
        return None


def iter_factor_dirs(root: Path):
    if not root.exists():
        return
    # recycle 是 recycle/{user}/{stage}/AlphaXxx, staging/alpha_src 是 root/AlphaXxx
    if root.name == "recycle":
        for user_dir in root.iterdir():
            if not user_dir.is_dir():
                continue
            for stage_dir in user_dir.iterdir():
                if not stage_dir.is_dir():
                    continue
                for fdir in stage_dir.iterdir():
                    if fdir.is_dir() and (fdir / "meta.json").exists():
                        yield fdir
    else:
        for fdir in root.iterdir():
            if fdir.is_dir() and (fdir / "meta.json").exists():
                yield fdir


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    drift = []
    for root in ROOTS:
        for fdir in iter_factor_dirs(root):
            meta_path = fdir / "meta.json"
            try:
                meta = json.loads(meta_path.read_text())
            except Exception as e:
                print(f"  ! meta parse fail {fdir}: {e}", file=sys.stderr)
                continue
            meta_delay = meta.get("delay")
            xml_delay = alpha_delay_from_xml(fdir)
            if xml_delay is None:
                continue
            if meta_delay != xml_delay:
                drift.append((fdir, meta_path, meta, meta_delay, xml_delay))

    print(f"found {len(drift)} factor with meta.delay != xml.Alpha.@delay")
    for fdir, _, _, m, x in drift:
        print(f"  {fdir.name}  meta={m} -> xml={x}")

    if not args.apply:
        print("\n(dry-run; pass --apply to write)")
        return

    for fdir, meta_path, meta, _, xml_delay in drift:
        meta["delay"] = xml_delay
        tmp = meta_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
        tmp.replace(meta_path)
    print(f"\nupdated {len(drift)} meta.json")


if __name__ == "__main__":
    main()
