#!/usr/bin/env python3
"""bcorr 池对账:pnl_automated / pnl_manual ↔ PG 因子状态。

池副本的应然状态(repo.purge_artifacts 的 CHECK 面政策):**池里有副本 ⇔ 因子
ACTIVE 在库**。离库(rejected / restage / rm)时副本应被回收 —— 政策上线前的
存量残留就是"鬼影":重检或新因子跑 bcorr 时撞上它,高相关分支被迫打败一个
已离库的对手 → 误拒(JOURNAL PV7 同族问题的存量面)。

用法(160,PG 可达;默认 dry-run 只读不删):
    uv run python scripts/reconcile_bcorr_pools.py            # 对账报告
    sudo $(command -v uv) run python scripts/reconcile_bcorr_pools.py --apply
                                                              # 删除鬼影(池文件 root-owned)

判定表(对池里每个文件,按文件名 = 因子名查 PG):
    PG 无记录            → ghost(已 rm / 从未入库)
    info 孤儿(无 state)→ ghost
    status != active     → ghost(离库副本未回收)
    active 但 discovery_method 与所在池不符 → wrong-pool(只报告,不删)
    active 且池匹配      → ok
另做反向检查(ACTIVE 因子缺池副本)—— 只报告:approve 放行的因子合法无池副本
(REJECTED 不拷池,approve 只翻状态),不能自动补。

安全:PG 只读(单条 find);--apply 只 unlink 两个池目录内被判 ghost 的文件,
不碰 alpha_pnl / dump / PG。本脚本是未来 ops doctor 池对账的种子。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ops.core.factor import Factor  # noqa: E402
from ops.core.state import FactorStatus  # noqa: E402
from ops.infra.config import Config, get_default_config_path  # noqa: E402
from ops.infra.repository import FactorRepository  # noqa: E402


def classify(factor: Factor | None, pool_kind: str) -> tuple[str, str]:
    """(verdict, reason);verdict ∈ {'ok', 'ghost', 'wrong-pool'}。"""
    if factor is None:
        return "ghost", "PG 无记录(已 rm / 从未入库)"
    if factor.state is None:
        return "ghost", "info 孤儿(有身份无状态)"
    if factor.state.status != FactorStatus.ACTIVE:
        return "ghost", f"status={factor.state.status.value}(离库副本未回收)"
    dm = factor.identity.discovery_method
    if dm in ("automated", "manual") and dm != pool_kind:
        return "wrong-pool", f"discovery_method={dm} 却在 pnl_{pool_kind}"
    return "ok", ""


def main() -> int:
    parser = argparse.ArgumentParser(description="bcorr 池 ↔ PG 状态对账")
    parser.add_argument("--config-path", "-c", type=Path,
                        default=get_default_config_path())
    parser.add_argument("--apply", action="store_true",
                        help="删除判定为 ghost 的池文件(默认 dry-run 只报告)")
    args = parser.parse_args()

    config = Config.load(args.config_path)
    pools = {"automated": config.pnl_automated, "manual": config.pnl_manual}
    for kind, d in pools.items():
        if not d.is_dir():
            print(f"错误: 池目录不存在 pnl_{kind}: {d}", file=sys.stderr)
            return 2

    factors = {x.identity.name: x
               for x in FactorRepository(config).find(include_submitted=True)}
    print(f"PG 因子记录: {len(factors)}")

    ghosts: list[tuple[Path, str]] = []
    wrong: list[tuple[Path, str]] = []
    skipped: list[Path] = []
    ok = 0
    for kind, pool_dir in pools.items():
        for f in sorted(pool_dir.iterdir()):
            if not f.is_file() or f.name.startswith("."):
                skipped.append(f)  # 目录 / 隐藏物不是池副本形态,人工看
                continue
            verdict, reason = classify(factors.get(f.name), kind)
            if verdict == "ghost":
                ghosts.append((f, reason))
            elif verdict == "wrong-pool":
                wrong.append((f, reason))
            else:
                ok += 1

    print(f"池副本 OK: {ok}")
    print(f"鬼影 ghost: {len(ghosts)}")
    for f, reason in ghosts:
        print(f"  [ghost] {f.parent.name}/{f.name}  —  {reason}")
    if wrong:
        print(f"错池 wrong-pool(只报告,不删): {len(wrong)}")
        for f, reason in wrong:
            print(f"  [wrong-pool] {f.parent.name}/{f.name}  —  {reason}")
    if skipped:
        print(f"跳过(非单文件形态,人工确认): {len(skipped)}")
        for f in skipped:
            print(f"  [skip] {f}")

    # 反向:ACTIVE + 来源明确但池里没副本(approve 路径合法如此,只报告)
    missing = [
        name for name, x in sorted(factors.items())
        if x.state is not None and x.state.status == FactorStatus.ACTIVE
        and x.identity.discovery_method in pools
        and not (pools[x.identity.discovery_method] / name).is_file()
    ]
    if missing:
        print(f"ACTIVE 缺池副本(approve 豁免属合法,只报告): {len(missing)}")
        for name in missing:
            print(f"  [missing] {name}")

    if not args.apply:
        print("\ndry-run 结束(未删除任何文件;删除加 --apply)")
        return 0

    failed = 0
    for f, reason in ghosts:
        try:
            f.unlink()
            print(f"已删除 {f}")
        except OSError as e:
            failed += 1
            print(f"删除失败 {f}: {e}", file=sys.stderr)
    print(f"\napply 结束: 删除 {len(ghosts) - failed}/{len(ghosts)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
