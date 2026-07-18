#!/usr/bin/env python
"""影子对拍:存量归档 XML 的生产化副本 → scratch 真跑 → 与现役 dataset 逐字节 diff。

接管序列第 3 步的实弹验证(docs/design/factor-produce-v3.md §8/§9):用**真实
存量 XML**(非 e2e 模板)验证 prodxml 规则表 + gsim 位级一致性。全程对生产
零改动:alpha_src 只读(生产化发生在 scratch 副本上)、dataset 只读(diff 参照)、
一切写入只进 --scratch。

判读口径(AUDIT-DUMP-CONSISTENCY 先例):byte-equal 是唯一"干净";ATOL-equal
(数值同、字节异)单列存疑;drift/missing 即闸门不过(退出码 1)。dataset 尾部
~5 天每日被 cchang 续跑重写,与我们的运行存在数据时点差 —— **--enddate 建议
钉在数天前的交易日**,尾部异常单独看。

用法(170;跑 gsim,建议 -w 4;20 因子全段 ≈ 半小时级):
    uv run python scripts/produce_shadow_diff.py --scratch /tmp/shadow \\
        --enddate 20260714 --sample 20
    uv run python scripts/produce_shadow_diff.py --scratch /tmp/shadow \\
        --enddate 20260714 AlphaXxx AlphaYyy
"""
from __future__ import annotations

import argparse
import random
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                                              # noqa: E402

from ops.core.dumpfiles import iter_dump_files                  # noqa: E402
from ops.core.prodxml import ProdParams, productionize          # noqa: E402
from ops.infra.config import Config, get_default_config_path    # noqa: E402
from ops.infra.gsim.runner import Runner                        # noqa: E402
from ops.utils.xmlio import load_xml, save_xml                  # noqa: E402

ATOL = 1e-6


def shadow_params(config: Config, scratch: Path, enddate: str) -> ProdParams:
    """生产参数,仅三根重定向 scratch + enddate 钉死 —— 规则本身分毫不动,
    对拍才有效力。"""
    p = ProdParams.from_config(config)
    return replace(p,
                   enddate=enddate,
                   dump_root=str(scratch / "alpha_dump"),
                   pnl_root=str(scratch / "alpha_pnl"),
                   checkpoint_root=str(scratch / "checkpoint"))


def prepare_shadow_xml(config: Config, name: str, params: ProdParams,
                       scratch: Path) -> Path:
    """alpha_src XML → 生产化副本落 scratch(原件零改动;@module 经稳定前缀
    指向真实 alpha_src 的 .py,只读引用)。"""
    src = config.alpha_src / name
    xmls = sorted(src.glob("*.xml"))
    if not xmls:
        raise FileNotFoundError(f"{src} 无 XML")
    cfg = load_xml(xmls[0])
    productionize(cfg, name=name, params=params)
    out = scratch / "xml" / f"{name}.xml"
    out.parent.mkdir(parents=True, exist_ok=True)
    save_xml(out, cfg)
    return out


def run_one(name: str, config_path: Path, scratch: str, enddate: str,
            ) -> tuple[str, str]:
    """worker:生产化副本 + 真跑。返回 (name, err);err 空串 = 成功。"""
    try:
        config = Config.load(config_path)
        params = shadow_params(config, Path(scratch), enddate)
        xml = prepare_shadow_xml(config, name, params, Path(scratch))
        Runner.run_backtest(xml, config)
        return name, ""
    except Exception as e:
        return name, str(e)[:300]


def diff_factor(name: str, shadow_dump: Path, dataset_root: Path,
                ) -> tuple[dict[str, int], list[str]]:
    """逐日逐版本比对,返回 (计数, drift 明细行)。"""
    counts = {"byte": 0, "atol": 0, "drift": 0, "missing": 0}
    details: list[str] = []
    for date, ver, f in sorted(iter_dump_files(shadow_dump / name)):
        ref = dataset_root / name / str(date)[:4] / str(date)[4:6] / f.name
        if not ref.exists():
            counts["missing"] += 1
            details.append(f"{name} {date}{ver} MISSING-in-dataset")
            continue
        if f.read_bytes() == ref.read_bytes():
            counts["byte"] += 1
            continue
        a, b = np.load(f), np.load(ref)
        if a.shape == b.shape:
            d = np.abs(np.where(np.isnan(a) & np.isnan(b), 0,
                                np.where(np.isnan(a) | np.isnan(b), np.inf,
                                         a - b)))
            mx = float(np.nanmax(d)) if d.size else 0.0
            if mx < ATOL:
                counts["atol"] += 1
                details.append(f"{name} {date}{ver} ATOL-equal(字节异)")
                continue
            counts["drift"] += 1
            details.append(f"{name} {date}{ver} DRIFT maxdiff={mx:.3e}")
        else:
            counts["drift"] += 1
            details.append(f"{name} {date}{ver} SHAPE {a.shape}!={b.shape}")
    return counts, details


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("factors", nargs="*", help="显式因子名(与 --sample 二选一)")
    ap.add_argument("--config-path", "-c", type=Path,
                    default=get_default_config_path())
    ap.add_argument("--scratch", type=Path, required=True,
                    help="一切写入的落点(dump/pnl/checkpoint/xml 副本)")
    ap.add_argument("--enddate", required=True,
                    help="钉死的对拍截止交易日 YYYYMMDD(建议数天前,避开 dataset 尾部重写窗)")
    ap.add_argument("--sample", type=int, default=0,
                    help="从 alpha_src ∩ dataset 交集定种随机抽 N 个(seed=0,可复现)")
    ap.add_argument("--dataset", type=Path, default=None,
                    help="参照 dataset 根(缺省 = config produce.dump_root)")
    ap.add_argument("--workers", "-w", type=int, default=4)
    ap.add_argument("--report", type=Path, default=None)
    args = ap.parse_args()

    if not (len(args.enddate) == 8 and args.enddate.isdigit()):
        print(f"--enddate 需要 YYYYMMDD,得到 {args.enddate!r}")
        return 1

    config = Config.load(args.config_path)
    dataset_root = args.dataset or Path(ProdParams.from_config(config).dump_root)

    names = list(args.factors)
    if not names:
        if not args.sample:
            print("给显式因子名或 --sample N")
            return 1
        in_src = {d.name for d in config.alpha_src.iterdir()
                  if d.is_dir() and d.name.startswith("Alpha")}
        in_ds = {d.name for d in dataset_root.iterdir() if d.is_dir()}
        pool = sorted(in_src & in_ds)
        names = sorted(random.Random(0).sample(pool, min(args.sample, len(pool))))

    print(f"影子对拍: {len(names)} 因子, enddate={args.enddate}, "
          f"dataset={dataset_root}, scratch={args.scratch}")

    failures: list[tuple[str, str]] = []
    done: list[str] = []
    if args.workers <= 1:
        results = [run_one(n, args.config_path, str(args.scratch), args.enddate)
                   for n in names]
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool_:
            futs = [pool_.submit(run_one, n, args.config_path,
                                 str(args.scratch), args.enddate) for n in names]
            results = [f.result() for f in as_completed(futs)]
    for name, err in results:
        (failures.append((name, err)) if err else done.append(name))
        print(f"  {'✘' if err else '✔'} {name}" + (f": {err}" if err else ""))

    total = {"byte": 0, "atol": 0, "drift": 0, "missing": 0}
    lines = [f"# produce-shadow-diff {datetime.now():%Y%m%d-%H%M%S} "
             f"enddate={args.enddate} dataset={dataset_root}", ""]
    shadow_dump = args.scratch / "alpha_dump"
    for name in sorted(done):
        counts, details = diff_factor(name, shadow_dump, dataset_root)
        for k in total:
            total[k] += counts[k]
        lines.append(f"{name}: byte={counts['byte']} atol={counts['atol']} "
                     f"drift={counts['drift']} missing={counts['missing']}")
        lines.extend(f"    {d}" for d in details)
    for name, err in failures:
        lines.append(f"{name}: RUN-FAILED {err}")

    summary = (f"因子 {len(names)} (跑失败 {len(failures)}) | "
               f"byte-equal {total['byte']} | atol-equal {total['atol']} | "
               f"drift {total['drift']} | missing {total['missing']}")
    lines += ["", summary]
    report = args.report or Path(f"shadow-diff-{datetime.now():%Y%m%d-%H%M%S}.txt")
    report.write_text("\n".join(lines), encoding="utf-8")
    print(summary)
    print(f"报告: {report}")

    clean = not failures and total["drift"] == 0 and total["missing"] == 0
    print("对拍:" + ("通过(byte/atol 全等)" if clean else "不过 —— 见报告"))
    return 0 if clean else 1


if __name__ == "__main__":
    sys.exit(main())
