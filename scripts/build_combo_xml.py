#!/usr/bin/env python
"""生成 author 级 combo XML 全套(/nvme125/production/combo 起点)。

谱系照抄现役(等效替换路径):
- mode0:delay1 在库因子腿 → Combo 组合优化 → combo dump;checkpoint 续跑
  (run_cp.py)。腿 = ops 库 delay1 ACTIVE(按名排序,顺序即 checkpoint 腿序号),
  读 `/nvme125/alpha_dump/`(cchang 现行 dataset)。
- mode1:**不重复计算** —— AlphaLoad 直接读本线 mode0 的 combo dump →
  Stats mode=1 出 pnl;无 checkpoint(run.py)。
- mode2:跨作者聚合(combo_eq 形态)—— 三条 combo dump → AlphaComboEqualProd
  → Stats mode=2(带中证1000基准);无 checkpoint。
- enddate 全部钉死 20260710(用户定的确定性验收窗)。

产物:`<out-dir>/<author>.mode0.xml|mode1.xml|combo_eq.mode2.xml`;落生产根需 sudo。

用法:
    uv run python scripts/build_combo_xml.py                 # 全部(mode0×3+mode1×3+mode2)
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

# 各作者的 combo 形态(照抄现役:fguo/combo_su10.xml、lhw|zxu/mode0.xml)
COMBOS = {
    "fguo": dict(combo="Combo_su10", container="fguo_su10",
                 attrs='window="900" max_depth="5" ndays="10"',
                 authors=("fguo", "Fguo")),     # 同一人的两种 author 写法
    "lhw": dict(combo="Combo_su10", container="lhw",
                attrs='window="900" max_depth="5" ndays="10"',
                authors=("lhw",)),
    "zxu": dict(combo="Combo_su10", container="zxu",
                attrs='window="900" max_depth="5" ndays="10"',
                authors=("zxu",)),
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

_MODE0 = """<?xml version='1.0' encoding='utf-8'?>
<gsim>
    <Constants backdays="256" niodatapath="/nvme125/datasvc/data/cc" niomapprivate="true" authorWeight="{author}:1.0," time_intensive="false" product_id="combo_{author}" checkpointDays="5" checkpointDir="{root}/checkpoint/combo_{author}/"></Constants>
    <Universe startdate="20200101" enddate="{enddate}" secID="/datasvc/rawdata/secID" holidaysfile="/datasvc/rawdata/holidays" calendarfile="/datasvc/rawdata/wind_calendar.csv"></Universe>
    <Modules>
{base_data}
        <Combo id="{combo}" module="/usr/local/gsim/combo_src/{combo}.cpython-310-x86_64-linux-gnu.so"></Combo>
    </Modules>
    <Portfolio id="Portfolio" booksize="20e6" homecurrency="CNY">
        <Stats module="StatsSimpleV6" mode="0" index_ret="Dmgr_MktRet.mkt_avg_ret" thres="90"
            tradePrice="close" tax="0." fee="0." slippage="0." printStats="true"
            dumpPnl="true" pnlDir="{root}/pnl/{author}/mode0/"></Stats>
        <Alphas id="{container}" universeId="TOP3000" booksize="20e6" delay="1"
            combo="{combo}" {attrs}
            dumpAlphaCombo="true" dumpAlphaFile="true" dumpAlphaDir="{root}/dump/" moduleId="Alpha" lg="240">
{legs}
            <Operations>
                <Operation module="AlphaOpVectorNeutralize" factor="equ_factor_return.Alpha20"/>
                <Operation module="AlphaOpPower" exp="1.0"></Operation>
            </Operations>
        </Alphas>
    </Portfolio>
</gsim>
"""

_MODE1 = """<?xml version='1.0' encoding='utf-8'?>
<gsim>
    <Constants backdays="256" niodatapath="/nvme125/datasvc/data/cc" niomapprivate="true" authorWeight="{author}:1.0," time_intensive="false" product_id="combo_{author}"></Constants>
    <Universe startdate="20200101" enddate="{enddate}" secID="/datasvc/rawdata/secID" holidaysfile="/datasvc/rawdata/holidays" calendarfile="/datasvc/rawdata/wind_calendar.csv"></Universe>
    <Modules>
{base_data}
    </Modules>
    <Portfolio id="Portfolio" booksize="20e6" homecurrency="CNY">
        <Stats module="StatsSimpleV6" mode="1" index_ret="Dmgr_MktRet.mkt_avg_ret" thres="90"
            tradePrice="close" tax="0." fee="0." slippage="0." printStats="true"
            dumpPnl="true" pnlDir="{root}/pnl/{author}/mode1/"></Stats>
        <Alpha id="{container}" module="AlphaLoad" universeId="TOP3000" alphaDir="{root}/dump" ver="v2"></Alpha>
        <Operations>
            <Operation module="AlphaOpVectorNeutralize" factor="equ_factor_return.Alpha20"/>
            <Operation module="AlphaOpPower" exp="1.0"></Operation>
        </Operations>
    </Portfolio>
</gsim>
"""

# mode2 = 现役 combo_eq.xml 形态(跨作者等权聚合 + 中证1000基准 + 中性化)
_MODE2 = """<?xml version='1.0' encoding='utf-8'?>
<gsim>
    <Constants backdays="256" niodatapath="/nvme125/datasvc/data/cc" niomapprivate="true" authorWeight="combo_eq:1.0," time_intensive="false" product_id="combo_eq"></Constants>
    <Universe startdate="20200101" enddate="{enddate}" secID="/datasvc/rawdata/secID" holidaysfile="/datasvc/rawdata/holidays" calendarfile="/datasvc/rawdata/wind_calendar.csv"></Universe>
    <Modules>
        <Data id="ALL" module="UmgrAll" path=""></Data>
        <Data id="ALL_TRD" module="UmgrTrd" path=""></Data>
        <Data id="Basedata" module="DmgrBasedata" rawpricePath="" industryPath="" ST="" path="" niomapprivate="true"></Data>
        <Data id="PriceLimit" module="DmgrPriceLimit" dataPath="" path=""></Data>
        <Data id="adjfactor" module="DmgrAdjfactor" dataPath="" niomapprivate="true" path=""></Data>
        <Data id="adjprice" module="DmgrAdjprice" niomapprivate="true" path=""></Data>
        <Data id="ipo" module="DmgrIPO" dataPath="" path=""></Data>
        <Data id="ashareeodprices" module="Dmgrashareeodprices" dataPath="" niomapprivate="true"></Data>
        <Data id="aindexeodprices" module="Dmgraindexeodprices" dataPath="" niomapprivate="true"></Data>
        <Data id="HS300" module="/usr/local/gsim/source_ref/umgr_index.py" dataPath="/datasvc/rawdata_wind/HS300/" niomapprivate="true"></Data>
        <Data id="ZZ500" module="/usr/local/gsim/source_ref/umgr_index.py" dataPath="/datasvc/rawdata_wind/ZZ500/" niomapprivate="true"></Data>
        <Data id="DmgrWbai_AIndexCSI500Weight" module="/usr/local/gsim/source_ref/DmgrWbai_AIndexCSI500Weight.py" dataPath="/datasvc/rawdata_wind/AIndexCSI500Weight/" niomapprivate="true"></Data>
        <Data id="DmgrWbai_AIndexCSI1000Weight" module="/usr/local/gsim/source_ref/DmgrWbai_AIndexCSI1000Weight.py" dataPath="/datasvc/rawdata_wind/AIndexCSI500Weight/" niomapprivate="true"></Data>
        <Data id="TOP3000" module="/usr/local/gsim/source_ref/umgr_topliquid.py" univsize="3000"  niomapprivate="true"></Data>
        <Data id="Dmgr_adv20" module="/usr/local/gsim/dm_src/Dmgr_advN.py" ndays="20" nioimapprivate="true"></Data>
        <Data id="Dmgr_MktRet" module="/usr/local/gsim/dm_src/Dmgr_MktRet.py" niomapprivate="true"></Data>
        <Data id="equ_factor_return" module="Dmgrequ_factor_return" dataPath="/datasvc/rawdata/rawdata_datayes/equ_factor_return" niomapprivate="true" ></Data>
    </Modules>
    <Portfolio id="Portfolio" booksize="20e6" homecurrency="CNY">
        <Stats module="StatsSimpleV6" mode="2" index_ret="aindexeodprices.s_dq_pctchange_000852" thres="90"
            tradePrice="close" tax="0." fee="0." slippage="0." printStats="true"
            dumpPnl="true" pnlDir="{root}/pnl/combo_eq/mode2/"></Stats>
        <Alphas id="opt" universeId="TOP3000" booksize="20e6" delay="1" combo="AlphaComboEqualProd"
                dumpAlphaCombo="false" dumpAlphaFile="false" dumpAlphaDir="{root}/dump/" moduleId="Alpha" lg="240">
{legs}
            <Operations>
                <Operation module="AlphaOpVectorNeutralize" factor="equ_factor_return.Alpha20"/>
                <Operation module="AlphaOpPower" exp="1.0"/>
            </Operations>
        </Alphas>
    </Portfolio>
</gsim>
"""

_LEG0 = ('            <Alpha id="{name}" module="AlphaLoad" universeId="TOP3000"'
         ' alphaDir="' + ALPHA_DUMP + '/" ver="v2"></Alpha>')
_LEG2 = ('            <Alpha id="{cid}" module="AlphaLoad" universeId="TOP3000"'
         ' alphaDir="{root}/dump" ver="v2"/>')


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--author", choices=sorted(COMBOS), default=None,
                    help="只生成指定作者(缺省 = 全部三个 + mode2)")
    ap.add_argument("--out-dir", type=Path, default=Path(f"{PROD_ROOT}/xml"),
                    help="XML 落点(生产根需 sudo;试跑可指 /tmp)")
    ap.add_argument("--config-path", "-c", type=Path,
                    default=get_default_config_path())
    args = ap.parse_args()

    config = Config.load(args.config_path)
    repo = FactorRepository(config)
    active = repo.find(status="active")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    containers: list[str] = []
    for author, spec in COMBOS.items():
        if args.author and author != args.author:
            continue
        legs = sorted(f.name for f in active
                      if f.identity.author in spec["authors"]
                      and f.snapshot and f.snapshot.delay == 1)
        # 无 dump 的腿(ACTIVE 但未投产)排除 —— 进来就是 NaN 腿;名单照报
        missing = {n for n in legs if not (Path(ALPHA_DUMP) / n).is_dir()}
        legs = [n for n in legs if n not in missing]
        (args.out_dir / f"{author}.mode0.xml").write_text(_MODE0.format(
            author=author, combo=spec["combo"], container=spec["container"],
            attrs=spec["attrs"], root=PROD_ROOT, enddate=ENDDATE,
            base_data=_BASE_DATA,
            legs="\n".join(_LEG0.format(name=n) for n in legs)),
            encoding="utf-8")
        (args.out_dir / f"{author}.mode1.xml").write_text(_MODE1.format(
            author=author, container=spec["container"], root=PROD_ROOT,
            enddate=ENDDATE, base_data=_BASE_DATA), encoding="utf-8")
        containers.append(spec["container"])
        print(f"{author}: mode0 腿 {len(legs)}(排除未投产 {len(missing)})"
              f" + mode1 → {args.out_dir}")
        for n in sorted(missing)[:10]:
            print(f"  ⚠ 未投产,未入 XML: {n}")

    if not args.author:
        (args.out_dir / "combo_eq.mode2.xml").write_text(_MODE2.format(
            root=PROD_ROOT, enddate=ENDDATE,
            legs="\n".join(_LEG2.format(cid=c, root=PROD_ROOT)
                           for c in containers)), encoding="utf-8")
        print(f"combo_eq.mode2: 聚合 {containers} → {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
