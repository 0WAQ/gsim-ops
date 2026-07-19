#!/usr/bin/env python
"""分组产线建组:delay1 ACTIVE → groups/<author>/delay1/<gid>/(docs/design/factor-produce-groups.md)。

roster/ordinal 落 PG(produce_group 两表,语义真相),group.xml 与组目录是
DB 的派生物;code/ 是建组时从 alpha_src 拷贝的冻结副本(组不引用活代码)。
幂等:已在 active 组的因子跳过,重跑只封待产/单产池的新组;封组即转正
(单产注册移除,单产目录留作冷备)。gid 发号查库,永不复号。

安全模型(仓库红线:破坏性 opt-in):
- 缺省 dry-run:只出报告 + 首组样品 XML(形态验收用),盘面与库零改动;
- `--apply` + 确认(-y 跳过)才落盘写库。

用法(170,建议 check 空档执行;写共享盘需 sudo):
    uv run python scripts/bootstrap_groups.py                 # dry-run 报告 + 样品
    uv run python scripts/bootstrap_groups.py --apply -y      # 确认后执行
    uv run python scripts/bootstrap_groups.py --report /tmp/boot.txt
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ops.core.paths import FactorPaths  # noqa: E402
from ops.core.prodgroup import GroupParams, build_group_xml, next_gid, partition  # noqa: E402
from ops.infra.config import Config, get_default_config_path  # noqa: E402
from ops.infra.repository import FactorRepository  # noqa: E402
from ops.utils.xmlio import load_xml, save_xml  # noqa: E402


def _load_leg(name: str, config: Config):
    """读因子归档 XML。返回 cfg dict;缺 XML 抛 FileNotFoundError。"""
    src = FactorPaths.of(name, config).src
    xmls = sorted(src.glob("*.xml"))
    if not xmls:
        raise FileNotFoundError(f"{name}: alpha_src 无 XML({src})")
    return load_xml(xmls[0])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config-path", "-c", type=Path,
                    default=get_default_config_path())
    ap.add_argument("--apply", action="store_true",
                    help="落盘 + 写库(缺省 dry-run 只出报告)")
    ap.add_argument("--factor", "-f", nargs="+", default=None,
                    help="只封指定因子(试点/补封;须 delay1 ACTIVE 且未在组)")
    ap.add_argument("--group-size", type=int, default=None,
                    help="覆盖 config 的每组腿数(试点小组,如 5)")
    ap.add_argument("-y", "--yes", action="store_true", help="跳过确认")
    ap.add_argument("--report", type=Path, default=None,
                    help="报告落点(缺省 ./bootstrap-groups-<ts>.txt)")
    args = ap.parse_args()

    config = Config.load(args.config_path)
    if config.env_overrides:
        print(f"⚠ OPS_* 环境变量覆盖生效: {', '.join(config.env_overrides)} "
              "—— hosts 声明被压掉;确认有意为之,否则 unset 后重跑")
    params = GroupParams.maybe_from_config(config)
    if params is None:
        print("bootstrap_groups: config 缺 produce.grouped.root —— "
              "在 config.yaml 补齐(参考 template/config.yaml)")
        return 1
    root = Path(params.root)

    repo = FactorRepository(config)
    records = [(f.name, f.identity.author or "",
                f.snapshot.delay if f.snapshot else None)
               for f in repo.find(status="active")]
    delay1 = [(n, a, d) for n, a, d in records if d == 1]
    skipped_delay0 = len(records) - len(delay1)

    membership = repo.group_membership()
    pending = [(n, a, d) for n, a, d in delay1 if n not in membership]
    warnings: list[str] = []
    if args.factor:
        # 试点/补封:只封显式点名的(逐个校验 delay1 ACTIVE 且未在组)
        by_name = {n: (a, d) for n, a, d in delay1}
        pool = []
        for n in args.factor:
            if n in membership:
                warnings.append(f"  ⚠ {n}: 已在组 {membership[n]},跳过")
            elif n not in by_name:
                warnings.append(f"  ⚠ {n}: 非 delay1 ACTIVE,跳过")
            else:
                a, d = by_name[n]
                pool.append((n, a, d))
        pending = pool
    size = args.group_size or params.group_size
    specs = partition(pending, size)
    used_gids = {g.gid for g in repo.groups(active_only=False)}

    scope = f"点名 {len(args.factor)}" if args.factor else "全量"
    lines: list[str] = [
        f"# bootstrap-groups {datetime.now():%Y-%m-%d %H:%M:%S} apply={args.apply}",
        f"ACTIVE 总数 {len(records)} | delay1 {len(delay1)} | "
        f"delay0 跳过 {skipped_delay0}(归 jdw 盘中产线) | 范围 {scope} | "
        f"pending {len(pending)}",
        f"将封新组 {len(specs)}(每组 ≤{size})"] + warnings + [""]

    plan: list[tuple[str, object, dict]] = []      # (gid, spec, group_xml)
    deferred = 0
    for spec in specs:
        gid = next_gid(used_gids | {g for g, _, _ in plan})
        legs = []
        missing = []
        for name in spec.members:
            try:
                legs.append((name, _load_leg(name, config)))
            except FileNotFoundError as e:
                missing.append(str(e))
        if missing:
            deferred += 1
            lines.append(f"{gid} {spec.author} ({len(spec.members)} 腿): "
                         f"DEFERRED 缺 XML {len(missing)} 个 —— 留 pending")
            lines.extend(f"    {m}" for m in missing)
            continue
        res = build_group_xml(legs, params, spec.author, gid)
        if res.conflicts:
            deferred += 1
            lines.append(f"{gid} {spec.author} ({len(spec.members)} 腿): "
                         f"DEFERRED 冲突 {len(res.conflicts)} 条 —— 留 pending")
            lines.extend(f"    {c}" for c in res.conflicts)
            continue
        plan.append((gid, spec, res.gsim))
        lines.append(f"{gid} {spec.author} ({len(spec.members)} 腿): OK")

    lines.append("")
    lines.append(f"汇总: 将建 {len(plan)} 组 | DEFERRED {deferred} 组(留 pending, "
                 "解决后重跑本脚本即封)")

    if args.apply and plan and not args.yes:
        ans = input(f"确认建 {len(plan)} 组(落盘 {root} + 写库)? [y/N] "
                    ).strip().lower()
        if ans not in ("y", "yes"):
            print("已取消")
            return 0

    built = failed = 0
    if args.apply:
        # 共享产物根随建组一次就位(叶子不在,gsim Stats "不存在才建" 会撞并行竞态)
        for d in (params.dump_root, params.pnl_root):
            Path(d).mkdir(parents=True, exist_ok=True)
        for gid, spec, gsim in plan:
            gdir = Path(params.group_dir(spec.author, gid))
            try:
                (gdir / "code").mkdir(parents=True, exist_ok=True)
                (gdir / "checkpoint").mkdir(parents=True, exist_ok=True)
                for name in spec.members:
                    src = FactorPaths.of(name, config).src
                    dst = gdir / "code" / name
                    if dst.exists():
                        shutil.rmtree(dst)       # 幂等:重建同名冻结副本
                    shutil.copytree(src, dst)
                save_xml(gdir / "group.xml", gsim)
                repo.create_group(gid, spec.author, spec.delay,
                                  list(spec.members))
                for name in spec.members:
                    # 单产 → 组产转正:注册移除(单产目录留作冷备,checkpoint 保留)
                    repo.remove_single(name)
                built += 1
            except Exception as e:               # 单组失败不阻断,留现场待查
                failed += 1
                lines.append(f"{gid} {spec.author}: FAILED {e} "
                             f"(现场 {gdir} 待人工核)")
        lines.append(f"apply: 建成 {built} | 失败 {failed}")

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = args.report or Path(f"bootstrap-groups-{ts}.txt")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[-3:]))
    print(f"报告: {report_path}")
    if not args.apply and plan:
        # dry-run 样品:首组完整 XML,形态验收(生成物,不是真相源)
        gid, spec, gsim = plan[0]
        sample = report_path.with_suffix(".sample.xml")
        save_xml(sample, gsim)
        print(f"首组样品: {sample}({spec.author}/{gid}, {len(spec.members)} 腿)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
