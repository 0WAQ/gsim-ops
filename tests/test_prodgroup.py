"""core/prodgroup 单测:组划分决定论 + 组 XML 生成的不变量(无需 PG / gsim)。

钉的是 BATCH-PRODUCE-MECHANICS-RESULT 的硬约束在代码里的兑现:腿序=字典序
(顺序即 checkpoint 序号)、腿属性/子树整段照抄、@module 指冻结副本、
Data 同 id 冲突必报、Universe 不一致必报、静音只翻属性不重排。
"""
import xmltodict

from ops.core.prodgroup import (
    GroupParams,
    as_list,
    build_group_xml,
    group_legs,
    mute_legs,
    next_gid,
    partition,
)

PARAMS = GroupParams(root="/nvme125/production/alpha", group_size=500, workers=8)


def _factor_cfg(name: str, *, delay="1", extra_data: str = "",
                universe_enddate="TODAY") -> dict:
    return xmltodict.parse(f"""<gsim>
\t<Constants backdays="256" niodatapath="/nvme125/datasvc/data/cc_all" checkpointDays="5" checkpointDir="/nvme125/checkpoint/{name}/"></Constants>
\t<Universe startdate="20110101" enddate="{universe_enddate}" secID="/datasvc/rawdata/secID" holidaysfile="/datasvc/rawdata/holidays" calendarfile="/datasvc/rawdata/wind_calendar.csv"></Universe>
\t<Modules>
\t\t<Data id="ALL_TRD" module="UmgrTrd" path=""></Data>
\t\t<Data id="Basedata" module="DmgrBasedata" rawpricePath="" path=""></Data>{extra_data}
\t\t<Alpha id="{name}Mod" module="/mnt/storage/alphalib/alpha_src/{name}/{name}.py"></Alpha>
\t</Modules>
\t<Portfolio id="MyPort" booksize="20e6" homecurrency="CNY">
\t\t<Stats module="StatsSimpleV6" mode="0" tradePrice="close" dumpPnl="true" pnlDir="/nvme125/alpha_pnl"></Stats>
\t\t<Alpha id="{name}" module="{name}Mod" universeId="ALL_TRD" booksize="20e6" delay="{delay}" ndays="20" dumpAlphaFile="true" dumpAlphaDir="/nvme125/alpha_dump" st="20">
\t\t\t<Description name="{name}" author="t" delay="{delay}"></Description>
\t\t\t<Operations>
\t\t\t\t<Operation module="AlphaOpDecay" days="3"></Operation>
\t\t\t</Operations>
\t\t</Alpha>
\t</Portfolio>
</gsim>
""")


# ---------------------------------------------------------------------------
# partition / next_gid
# ---------------------------------------------------------------------------

def test_partition_filters_delay_and_groups_by_author():
    recs = [("AlphaB2", "lhw", 1), ("AlphaA1", "fguo", 1),
            ("AlphaD0", "lhw", 0),            # delay0 → 排除
            ("AlphaNx", "lhw", None),          # delay 未知 → 排除
            ("AlphaA2", "fguo", 1), ("AlphaB1", "lhw", 1)]
    specs = partition(recs, size=500)
    assert [(s.author, s.members) for s in specs] == [
        ("fguo", ("AlphaA1", "AlphaA2")),
        ("lhw", ("AlphaB1", "AlphaB2")),
    ]


def test_partition_chunks_and_deterministic():
    recs = [(f"Alpha{i:03d}", "fguo", 1) for i in range(5)]
    specs = partition(recs, size=2)
    assert [s.members for s in specs] == [
        ("Alpha000", "Alpha001"), ("Alpha002", "Alpha003"), ("Alpha004",)]
    assert partition(recs, size=2) == specs          # 决定论
    assert all(s.delay == 1 for s in specs)


def test_next_gid_never_reuses():
    assert next_gid(set()) == "g001"
    assert next_gid({"g001", "g002"}) == "g003"
    assert next_gid({"g002"}) == "g001"              # 空洞可填,但不复用已存在的


def test_swap_dbname_for_pilot_roster():
    from ops.infra.groups import _swap_dbname
    ci = "host=10.9.100.160 port=15432 dbname=ops user=ops password=x"
    assert _swap_dbname(ci, "ops_test") == \
        "host=10.9.100.160 port=15432 dbname=ops_test user=ops password=x"
    assert "dbname=ops_test" in _swap_dbname("dbname=ops user=ops", "ops_test")


# ---------------------------------------------------------------------------
# build_group_xml
# ---------------------------------------------------------------------------

def test_build_legs_sorted_and_verbatim():
    # 输入乱序,腿必须按名字字典序(顺序 = checkpoint 序号)
    legs = [("AlphaB", _factor_cfg("AlphaB")), ("AlphaA", _factor_cfg("AlphaA"))]
    res = build_group_xml(legs, PARAMS, "t", "g001")
    assert res.conflicts == []
    g = res.gsim["gsim"]
    alphas = as_list(g["Portfolio"]["Alpha"])
    assert [a["@id"] for a in alphas] == ["AlphaA", "AlphaB"]
    leg = alphas[0]
    # 私有属性与 delay 整段照抄
    assert leg["@delay"] == "1" and leg["@ndays"] == "20" and leg["@st"] == "20"
    assert leg["@module"] == "AlphaAMod"
    assert leg["Description"]["@name"] == "AlphaA"
    assert leg["Operations"]["Operation"]["@module"] == "AlphaOpDecay"
    # 仅覆盖 dump 两属性
    assert leg["@dumpAlphaFile"] == "true"
    assert leg["@dumpAlphaDir"] == "/nvme125/production/alpha/dump"


def test_build_rewrites_module_to_frozen_copy():
    res = build_group_xml([("AlphaA", _factor_cfg("AlphaA"))], PARAMS, "t", "g001")
    mods = as_list(res.gsim["gsim"]["Modules"]["Alpha"])
    assert mods[0]["@module"] == (
        "/nvme125/production/alpha/groups/t/delay1/g001/code/AlphaA/AlphaA.py")
    assert mods[0]["@id"] == "AlphaAMod"


def test_build_roots_and_skeleton():
    res = build_group_xml([("AlphaA", _factor_cfg("AlphaA"))], PARAMS, "t", "g001")
    g = res.gsim["gsim"]
    assert g["Constants"]["@checkpointDir"] == (
        "/nvme125/production/alpha/groups/t/delay1/g001/checkpoint/")
    assert g["Constants"]["@backdays"] == "256"       # 骨架属性照抄
    assert g["Portfolio"]["@booksize"] == "20e6"
    assert g["Portfolio"]["@id"] == "MyPort"
    assert g["Portfolio"]["Stats"]["@pnlDir"] == "/nvme125/production/alpha/pnl"
    assert g["Portfolio"]["Stats"]["@tradePrice"] == "close"


def test_build_data_dedup_and_conflict():
    same = build_group_xml(
        [("AlphaA", _factor_cfg("AlphaA")), ("AlphaB", _factor_cfg("AlphaB"))],
        PARAMS, "t", "g001")
    datas = as_list(same.gsim["gsim"]["Modules"]["Data"])
    assert len(datas) == 2                             # 同 id 同属性 → 去重
    conflict = build_group_xml(
        [("AlphaA", _factor_cfg("AlphaA")),
         ("AlphaB", _factor_cfg(
             "AlphaB", extra_data='<Data id="ALL_TRD" module="UmgrOTHER" path="x"></Data>'))],
        PARAMS, "t", "g001")
    assert conflict.gsim is None
    assert any("ALL_TRD" in c and "冲突" in c for c in conflict.conflicts)


def test_build_universe_mismatch_rejected():
    res = build_group_xml(
        [("AlphaA", _factor_cfg("AlphaA")),
         ("AlphaB", _factor_cfg("AlphaB", universe_enddate="20251231"))],
        PARAMS, "t", "g001")
    assert res.gsim is None
    assert any("Universe" in c for c in res.conflicts)


def test_build_missing_leg_rejected():
    cfg = _factor_cfg("AlphaA")
    del cfg["gsim"]["Portfolio"]["Alpha"]
    res = build_group_xml([("AlphaA", cfg)], PARAMS, "t", "g001")
    assert res.gsim is None
    assert any("Portfolio/Alpha" in c for c in res.conflicts)


# ---------------------------------------------------------------------------
# group_legs / mute_legs
# ---------------------------------------------------------------------------

def test_mute_only_flips_attributes_preserving_order():
    res = build_group_xml(
        [("AlphaB", _factor_cfg("AlphaB")), ("AlphaC", _factor_cfg("AlphaC")),
         ("AlphaA", _factor_cfg("AlphaA"))], PARAMS, "t", "g001")
    cfg = res.gsim
    assert group_legs(cfg) == ["AlphaA", "AlphaB", "AlphaC"]
    flipped = mute_legs(cfg, {"AlphaB"}, mute=True)
    assert flipped == ["AlphaB"]
    alphas = as_list(cfg["gsim"]["Portfolio"]["Alpha"])
    assert [a["@id"] for a in alphas] == ["AlphaA", "AlphaB", "AlphaC"]   # 序不动
    assert [a["@dumpAlphaFile"] for a in alphas] == ["true", "false", "true"]
    mute_legs(cfg, {"AlphaB"}, mute=False)
    assert as_list(cfg["gsim"]["Portfolio"]["Alpha"])[1]["@dumpAlphaFile"] == "true"
