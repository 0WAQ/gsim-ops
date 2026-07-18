#!/usr/bin/env python
"""存量归档 XML 一次性生产化迁移(docs/design/factor-produce-v3.md §9 步骤 2)。

对 alpha_src 下全部因子目录的 XML 原地套 `ops/core/prodxml` 三张规则表。
规则幂等 → 本脚本可重跑;已生产态的因子报告"无变化"跳过。REJECTED 一并
迁移 —— approve 可不经重检直接翻 ACTIVE,届时 XML 必须已是生产态。

安全模型(仓库红线:破坏性 opt-in):
- 缺省 dry-run:只产逐字段 diff 报告,盘面零改动;
- `--apply` + 确认(-y 跳过)才落盘;改前逐文件备份到 --backup-dir;
- apply 模式逐因子 factor_lock(跨机),占用即跳过(与 check/restage 不撞)。

用法(170,建议 check 空档执行;写共享盘需 sudo):
    uv run python scripts/migrate_prod_xml.py                 # dry-run 报告
    uv run python scripts/migrate_prod_xml.py --apply         # 确认后执行
    uv run python scripts/migrate_prod_xml.py -f AlphaXxx     # 单因子
    uv run python scripts/migrate_prod_xml.py --apply -y --report /tmp/mig.txt
"""
from __future__ import annotations

import argparse
import copy
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ops.core.prodxml import ProdParams, productionize          # noqa: E402
from ops.infra.config import Config, get_default_config_path    # noqa: E402
from ops.infra.lock import FactorLocked, factor_lock            # noqa: E402
from ops.utils.xmlio import load_xml, save_xml                  # noqa: E402


def _flatten(node, prefix="", out=None) -> dict[str, str]:
    """dict/list → {路径: 值}(仅 @属性),供逐字段 diff。"""
    if out is None:
        out = {}
    if isinstance(node, dict):
        for k, v in node.items():
            if k.startswith("@"):
                out[f"{prefix}/{k}"] = str(v)
            else:
                _flatten(v, f"{prefix}/{k}", out)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _flatten(item, f"{prefix}[{i}]", out)
    return out


def _diff(before: dict, after: dict) -> list[tuple[str, str, str]]:
    keys = sorted(set(before) | set(after))
    return [(k, before.get(k, "<absent>"), after.get(k, "<absent>"))
            for k in keys if before.get(k) != after.get(k)]


def migrate_one(xml_file: Path, name: str, params: ProdParams, *,
                apply: bool, backup_dir: Path | None):
    """返回 (status, diffs|err)。status ∈ changed/unchanged/failed。"""
    try:
        cfg = load_xml(xml_file)
        after = copy.deepcopy(cfg)
        productionize(after, name=name, params=params)
        diffs = _diff(_flatten(cfg), _flatten(after))
        if not diffs:
            return "unchanged", []
        if apply:
            if backup_dir is not None:
                dst = backup_dir / name / xml_file.name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(xml_file, dst)
            save_xml(xml_file, after)
        return "changed", diffs
    except Exception as e:                                       # 单因子失败不阻断
        return "failed", str(e)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config-path", "-c", type=Path,
                    default=get_default_config_path())
    ap.add_argument("--factor", "-f", default=None, help="只迁移指定因子")
    ap.add_argument("--apply", action="store_true",
                    help="落盘执行(缺省 dry-run 只出报告)")
    ap.add_argument("-y", "--yes", action="store_true", help="跳过确认")
    ap.add_argument("--backup-dir", type=Path, default=None,
                    help="apply 前逐文件备份根(缺省 <alphalib 根>/.migrate-prod-xml-bak-<ts>)")
    ap.add_argument("--report", type=Path, default=None,
                    help="逐字段 diff 报告落点(缺省 ./migrate-prod-xml-<ts>.txt)")
    args = ap.parse_args()

    config = Config.load(args.config_path)
    params = ProdParams.from_config(config)      # 缺键响亮,绝不半配置迁移

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = args.report or Path(f"migrate-prod-xml-{ts}.txt")
    backup_dir = None
    if args.apply:
        backup_dir = args.backup_dir or (
            config.alpha_src.parent / f".migrate-prod-xml-bak-{ts}")

    dirs = ([config.alpha_src / args.factor] if args.factor
            else sorted(d for d in config.alpha_src.iterdir()
                        if d.is_dir() and d.name.startswith("Alpha")))

    if args.apply and not args.yes:
        ans = input(f"确认迁移 {len(dirs)} 个因子的归档 XML(备份至 {backup_dir})? "
                    "[y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("已取消")
            return 0

    changed = unchanged = failed = no_xml = locked = 0
    lines: list[str] = [f"# migrate-prod-xml {ts} apply={args.apply}", ""]
    for d in dirs:
        name = d.name
        xmls = sorted(d.glob("*.xml"))
        if not xmls:
            no_xml += 1
            lines.append(f"{name}: NO-XML")
            continue
        if args.apply:
            try:
                with factor_lock(name, config):
                    status, detail = migrate_one(
                        xmls[0], name, params, apply=True, backup_dir=backup_dir)
            except FactorLocked:
                locked += 1
                lines.append(f"{name}: LOCKED(check/restage 占用,跳过)")
                continue
        else:
            status, detail = migrate_one(
                xmls[0], name, params, apply=False, backup_dir=None)

        if status == "unchanged":
            unchanged += 1
        elif status == "failed":
            failed += 1
            lines.append(f"{name}: FAILED {detail}")
        else:
            changed += 1
            lines.append(f"{name}: {len(detail)} 字段")
            lines.extend(f"    {k}: {old!r} -> {new!r}" for k, old, new in detail)

    summary = (f"总数 {len(dirs)} | 将改/已改 {changed} | 已生产态 {unchanged} | "
               f"无XML {no_xml} | 占用 {locked} | 失败 {failed} | apply={args.apply}")
    lines += ["", summary]
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(summary)
    print(f"逐字段报告: {report_path}")
    if args.apply and backup_dir is not None and changed:
        print(f"备份: {backup_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
