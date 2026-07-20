#!/usr/bin/env python
"""生成 combo XML 全套(/nvme125/production/combo 起点,4 combo × 3 mode)。

谱系(用户 2026-07-20 定稿):
- 四个 combo:fguo / lhw / zxu(author 级,腿 = 各自 delay1 在库因子)+
  combo_eq(把三个 author combo 再聚合);容器命名一律作者名,无特例。
- 每个 combo 三 mode:
  mode0 = 计算(腿 → Combo_su10 组合优化 → combo dump;checkpoint 续跑,run_cp.py);
  mode1 = AlphaLoad 读本线 dump → Stats mode=1 pnl(run.py,不重算);
  mode2 = 同 mode1 但 Stats mode=2(中证1000 基准)。
- 全部:Combo_su10、TOP3000 universe、全 mode 中性化(Neutralize→Power)、
  startdate 20200101(2011 因 lhw/zxu 数据全 NaN 被 LGBM 零样本拒,回现役口径)、enddate 钉死 20260710(验收窗)。
- Data 套件镜像现役 combo_eq(TOP3000/中性化的配套依赖:HS300/ZZ500/
  AIndexWeight/adv20/asharebalancesheet/equ_factor_return)。

产物:`<out-dir>/<combo>.mode{0,1,2}.xml`;落生产根需写权限(wbai:alpha-core)。

用法:
    uv run python scripts/build_combo_xml.py                 # 全部 12 份
    uv run python scripts/build_combo_xml.py --author fguo --out-dir /tmp/x
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ops.infra.config import Config, get_default_config_path      # noqa: E402
from ops.infra.repository import FactorRepository                 # noqa: E402

PROD_ROOT = "/nvme125/production/combo"
ALPHA_DUMP = "/nvme125/alpha_dump"      # mode0 腿的 alphaDir(cchang 现行 dataset)
ENDDATE = "20260710"                    # 用户钉死的验收窗
SU10 = 'window="900" max_depth="5" ndays="10"'
EQPROD = ''   # lg="240" 已在 _MODE0_BODY 模板里;AlphaComboEqualProd 无额外属性

# 各作者的腿来源(author 大小写已归一,见 migrate_author_case.sql)
COMBOS = {
    "fguo": ("fguo",),
    "lhw": ("lhw",),
    "zxu": ("zxu",),
}

_BASE_DATA = """\
        <Data id="ALL" module="UmgrAll" path=""></Data>
        <Data id="ALL_TRD" module="UmgrTrd" path=""></Data>
        <Data id="Basedata" module="DmgrBasedata" rawpricePath="" industryPath="" ST="" path="" niomapprivate="true"></Data>
        <Data id="PriceLimit" module="DmgrPriceLimit" dataPath="" path=""></Data>
        <Data id="adjfactor" module="DmgrAdjfactor" dataPath="" path=""></Data>
        <Data id="adjprice" module="DmgrAdjprice" path=""></Data>
        <Data id="ipo" module="DmgrIPO" dataPath="" path=""></Data>
        <Data id="ashareeodprices" module="Dmgrashareeodprices" dataPath="" niomapprivate="true"></Data>
        <Data id="aindexeodprices" module="Dmgraindexeodprices" dataPath="" niomapprivate="true"></Data>
        <Data id="HS300" module="/usr/local/gsim/source_ref/umgr_index.py" dataPath="/datasvc/rawdata_wind/HS300/" niomapprivate="true"></Data>
        <Data id="ZZ500" module="/usr/local/gsim/source_ref/umgr_index.py" dataPath="/datasvc/rawdata_wind/ZZ500/" niomapprivate="true"></Data>
        <Data id="DmgrWbai_AIndexCSI500Weight" module="/usr/local/gsim/source_ref/DmgrWbai_AIndexCSI500Weight.py" dataPath="/datasvc/rawdata_wind/AIndexCSI500Weight/" niomapprivate="true"></Data>
        <Data id="DmgrWbai_AIndexCSI1000Weight" module="/usr/local/gsim/source_ref/DmgrWbai_AIndexCSI1000Weight.py" dataPath="/datasvc/rawdata_wind/AIndexCSI500Weight/" niomapprivate="true"></Data>
        <Data id="Dmgr_adv20" module="/usr/local/gsim/dm_src/Dmgr_advN.py" ndays="20" nioimapprivate="true"></Data>
        <Data id="asharebalancesheet" module="/usr/local/gsim/source_ref/Dmgr_asharebalancesheet.py" dataPath="/datasvc/rawdata_wind/asharebalancesheet" niomapprivate="true"></Data>
        <Data id="TOP3000" module="/usr/local/gsim/source_ref/umgr_topliquid.py" univsize="3000" niomapprivate="true"></Data>
        <Data id="equ_factor_return" module="Dmgrequ_factor_return" dataPath="/datasvc/rawdata/rawdata_datayes/equ_factor_return" niomapprivate="true"></Data>
        <Data id="Dmgr_MktRet" module="/usr/local/gsim/dm_src/Dmgr_MktRet.py" niomapprivate="true"></Data>"""

_HEAD = """<?xml version='1.0' encoding='utf-8'?>
<gsim>
    <Constants backdays="256" niodatapath="/nvme125/datasvc/data/cc" niomapprivate="true" authorWeight="{weight}:1.0," time_intensive="false" product_id="{pid}"{ckpt}></Constants>
    <Universe startdate="20200101" enddate="{enddate}" secID="/datasvc/rawdata/secID" holidaysfile="/datasvc/rawdata/holidays" calendarfile="/datasvc/rawdata/wind_calendar.csv"></Universe>
    <Modules>
{base_data}{combo_mod}
    </Modules>
"""

_STATS = """\
        <Stats module="StatsSimpleV6" mode="{mode}" index_ret="{index_ret}" thres="90"
            tradePrice="close" tax="0." fee="0." slippage="0." printStats="true"
            dumpPnl="true" pnlDir="{root}/pnl/{owner}/mode{mode}/"></Stats>"""

_OPS = """\
            <Operations>
                <Operation module="AlphaOpVectorNeutralize" factor="equ_factor_return.Alpha20"/>
                <Operation module="AlphaOpPower" exp="1.0"></Operation>
            </Operations>"""

_MODE0_BODY = """\
    <Portfolio id="Portfolio" booksize="20e6" homecurrency="CNY">
{stats}
        <Alphas id="{container}" universeId="TOP3000" booksize="20e6" delay="1"
            combo="{combo}" {attrs}
            dumpAlphaCombo="true" dumpAlphaFile="true" dumpAlphaDir="{root}/dump/" moduleId="Alpha" lg="240">
{legs}
        </Alphas>
{ops}
    </Portfolio>
</gsim>
"""

_PNL_BODY = """\
    <Portfolio id="Portfolio" booksize="20e6" homecurrency="CNY">
{stats}
        <Alpha id="{container}" module="AlphaLoad" universeId="TOP3000" alphaDir="{root}/dump" ver="v2"></Alpha>
{ops}
    </Portfolio>
</gsim>
"""

_LEG0 = ('            <Alpha id="{name}" module="AlphaLoad" universeId="TOP3000"'
         ' alphaDir="' + ALPHA_DUMP + '/" ver="v2"></Alpha>')
_LEG_EQ = ('            <Alpha id="{cid}" module="AlphaLoad" universeId="TOP3000"'
           ' alphaDir="' + PROD_ROOT + '/dump" ver="v2"/>')

_INDEX = {0: "aindexeodprices.s_dq_pctchange_000852",
          1: "aindexeodprices.s_dq_pctchange_000852",
          2: "aindexeodprices.s_dq_pctchange_000852"}


def _head(weight: str, pid: str, ckpt_dir: str | None,
          combo_module: str | None) -> str:
    ckpt = f' checkpointDays="5" checkpointDir="{ckpt_dir}"' if ckpt_dir else ""
    combo_mod = (f'\n        <Combo id="{combo_module}" '
                 f'module="/usr/local/gsim/combo_src/{combo_module}'
                 '.cpython-310-x86_64-linux-gnu.so"></Combo>' if combo_module else "")
    return _HEAD.format(weight=weight, pid=pid, ckpt=ckpt,
                        enddate=ENDDATE, base_data=_BASE_DATA,
                        combo_mod=combo_mod)


def _stats(owner: str, mode: int) -> str:
    return _STATS.format(mode=mode, index_ret=_INDEX[mode], root=PROD_ROOT,
                         owner=owner)


def mode0_xml(author: str, legs: list[str]) -> str:
    return (_head(author, f"combo_{author}",
                  f"{PROD_ROOT}/checkpoint/combo_{author}/", "Combo_su10")
            + _MODE0_BODY.format(
                stats=_stats(author, 0), container=author, combo="Combo_su10",
                attrs=SU10, root=PROD_ROOT,
                legs="\n".join(_LEG0.format(name=n) for n in legs), ops=_OPS))


def pnl_xml(owner: str, mode: int) -> str:
    return (_head(owner, f"combo_{owner}", None, None)
            + _PNL_BODY.format(stats=_stats(owner, mode), container=owner,
                               root=PROD_ROOT, ops=_OPS))


def combo_eq_mode0_xml(containers: list[str]) -> str:
    return (_head("combo_eq", "combo_eq",
                  f"{PROD_ROOT}/checkpoint/combo_eq/", None)
            + _MODE0_BODY.format(
                stats=_stats("combo_eq", 0), container="combo_eq",
                combo="AlphaComboEqualProd", attrs=EQPROD, root=PROD_ROOT,
                legs="\n".join(_LEG_EQ.format(cid=c) for c in containers),
                ops=_OPS))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--author", choices=sorted(COMBOS) + ["combo_eq"], default=None,
                    help="只生成指定 combo(缺省 = 全部 4 个 × 3 mode)")
    ap.add_argument("--out-dir", type=Path, default=Path(f"{PROD_ROOT}/xml"),
                    help="XML 落点(试跑可指 /tmp)")
    ap.add_argument("--config-path", "-c", type=Path,
                    default=get_default_config_path())
    args = ap.parse_args()

    config = Config.load(args.config_path)
    repo = FactorRepository(config)
    active = repo.find(status="active")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    containers: list[str] = []
    for author, authors in COMBOS.items():
        if args.author not in (None, author):
            continue
        legs = sorted(f.name for f in active
                      if f.identity.author in authors
                      and f.snapshot and f.snapshot.delay == 1)
        # 无 dump 的腿(ACTIVE 但未投产)排除 —— 进来就是 NaN 腿;名单照报
        missing = {n for n in legs if not (Path(ALPHA_DUMP) / n).is_dir()}
        legs = [n for n in legs if n not in missing]
        for mode, content in ((0, mode0_xml(author, legs)),
                              (1, pnl_xml(author, 1)), (2, pnl_xml(author, 2))):
            (args.out_dir / f"{author}.mode{mode}.xml").write_text(
                content, encoding="utf-8")
        containers.append(author)
        print(f"{author}: mode0 腿 {len(legs)}(排除未投产 {len(missing)})"
              f" + mode1/mode2 → {args.out_dir}")
        for n in sorted(missing)[:10]:
            print(f"  ⚠ 未投产,未入 XML: {n}")

    if args.author in (None, "combo_eq"):
        for mode, content in ((0, combo_eq_mode0_xml(containers)),
                              (1, pnl_xml("combo_eq", 1)),
                              (2, pnl_xml("combo_eq", 2))):
            (args.out_dir / f"combo_eq.mode{mode}.xml").write_text(
                content, encoding="utf-8")
        print(f"combo_eq: 聚合 {containers} × 3 mode → {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
