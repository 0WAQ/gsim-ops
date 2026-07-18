"""core/prodxml 单测:三张规则表逐条 + 幂等性(无需 PG / gsim)。

样本 XML 刻意塞满存量杂质的全部已知形态(cc_2025 / cache / data_local /
FULLMod / StatsSimpleV5 / 目录名≠py 名 / 非 datasvc 外部路径),一次生产化
后逐字段验收 —— 这即 docs/design/factor-produce-v3.md §4 的可执行规格。
"""
import copy

import pytest
import xmltodict

from ops.core.prodxml import ProdParams, productionize, productionize_file

PARAMS = ProdParams(
    nio_data_path="/nvme125/datasvc/data/cc_all",
    enddate="TODAY",
    startdate="20110101",
    backdays=256,
    checkpoint_root="/nvme125/checkpoint",
    dump_root="/nvme125/alpha_dump",
    pnl_root="/nvme125/alpha_pnl",
    datasvc_prefix="/nvme125",
    module_prefix="/mnt/storage/alphalib/alpha_src",
)

# 归档态样本:窗口残留 long_backtest、输出被拆雷指 /tmp、路径杂质全形态
_ARCHIVED = """<gsim>
\t<Constants backdays="320" niodatapath="/datasvc/data/cc_2025" niomapprivate="true" checkpointDir="/home/wbai/alpha/dropbox/checkpoint/AlphaX/" checkpointDays="5"></Constants>
\t<Universe startdate="20150101" enddate="20251231" secID="/datasvc/rawdata/secID" holidaysfile="/datasvc/rawdata/holidays" calendarfile="/datasvc/rawdata/wind_calendar.csv"></Universe>
\t<Modules>
\t\t<Data id="ALL_TRD" module="UmgrTrd" path=""></Data>
\t\t<Data id="FULLMod" module="DmgrFULL" niodatapath="/datasvc/data/cc_2025/FULL"></Data>
\t\t<Data id="fancy" module="Dmgrfancy" niodatapath="/cache/data/x"></Data>
\t\t<Data id="fivemin" module="Dmgr5m" dataPath="/home/fguo/data_local"></Data>
\t\t<Data id="wind" module="DmgrW" dataPath="/datasvc/rawdata_wind/HS300/"></Data>
\t\t<Data id="ext" module="DmgrExt" niodatapath="/somewhere/else"></Data>
\t\t<Alpha id="AlphaXMod" module="/tank/vault/alphalib/alpha_src/AlphaX/WeirdName.py"></Alpha>
\t</Modules>
\t<Portfolio id="P" booksize="20e6">
\t\t<Stats module="StatsSimpleV5" mode="0" dumpPnl="false" pnlDir="/tmp/alphalib/alpha_pnl"></Stats>
\t\t<Alpha id="AlphaX" module="AlphaXMod" delay="1" dumpAlphaFile="true" dumpAlphaDir="/tmp/alphalib/alpha_dump"></Alpha>
\t</Portfolio>
</gsim>
"""


def _productionized():
    cfg = xmltodict.parse(_ARCHIVED)
    productionize(cfg, name="AlphaX", params=PARAMS)
    return cfg["gsim"]


def test_set_rules():
    g = _productionized()
    c = g["Constants"]
    assert c["@niodatapath"] == "/nvme125/datasvc/data/cc_all"
    assert c["@backdays"] == "256"
    assert c["@checkpointDir"] == "/nvme125/checkpoint/AlphaX/"
    assert c["@checkpointDays"] == "5"
    assert g["Universe"]["@startdate"] == "20110101"
    assert g["Universe"]["@enddate"] == "TODAY"
    assert g["Portfolio"]["Stats"]["@dumpPnl"] == "true"
    assert g["Portfolio"]["Stats"]["@pnlDir"] == "/nvme125/alpha_pnl"
    assert g["Portfolio"]["Alpha"]["@dumpAlphaFile"] == "true"
    assert g["Portfolio"]["Alpha"]["@dumpAlphaDir"] == "/nvme125/alpha_dump"


def test_universe_exception_paths_untouched():
    """★ Universe 的 secID/holidays/calendar 必须保持 /datasvc(加前缀 →
    secpath 元数据不匹配 → 重建只读缓存崩)。"""
    u = _productionized()["Universe"]
    assert u["@secID"] == "/datasvc/rawdata/secID"
    assert u["@holidaysfile"] == "/datasvc/rawdata/holidays"
    assert u["@calendarfile"] == "/datasvc/rawdata/wind_calendar.csv"


def test_replace_legacy_then_prefix_migration():
    data = {d["@id"]: d for d in _productionized()["Modules"]["Data"]}
    # cc_2025 → cc_all → 加前缀(①a 后 ②)
    assert data["FULL"]["@niodatapath"] == "/nvme125/datasvc/data/cc_all/FULL"
    # /cache/data → /datasvc/data → 加前缀(①b 后 ②)
    assert data["fancy"]["@niodatapath"] == "/nvme125/datasvc/data/x"
    # data_local 整值 → 5min 表 → 加前缀(①c 后 ②)
    assert data["fivemin"]["@dataPath"] == \
        "/nvme125/datasvc/data/cc_all/cn_equity_feature_5min"
    # 一般 /datasvc 属性走通吃前缀迁移(②)
    assert data["wind"]["@dataPath"] == "/nvme125/datasvc/rawdata_wind/HS300/"
    # 非 datasvc 外部路径不动
    assert data["ext"]["@niodatapath"] == "/somewhere/else"


def test_stats_v5_upgraded():
    assert _productionized()["Portfolio"]["Stats"]["@module"] == "StatsSimpleV6"


def test_strip_only_data_id():
    g = _productionized()
    data_ids = {d["@id"] for d in g["Modules"]["Data"]}
    assert "FULL" in data_ids and "FULLMod" not in data_ids
    # Alpha 的 id 以 Mod 结尾是命名惯例,绝不能削
    assert g["Modules"]["Alpha"]["@id"] == "AlphaXMod"
    assert g["Portfolio"]["Alpha"]["@module"] == "AlphaXMod"


def test_module_keeps_original_basename():
    """目录名(AlphaX)≠ .py 名(WeirdName.py):文件名沿用原 basename。"""
    g = _productionized()
    assert g["Modules"]["Alpha"]["@module"] == \
        "/mnt/storage/alphalib/alpha_src/AlphaX/WeirdName.py"


def test_module_placeholder_falls_back_to_dirname():
    cfg = xmltodict.parse(_ARCHIVED.replace(
        'module="/tank/vault/alphalib/alpha_src/AlphaX/WeirdName.py"',
        'module="PLACEHOLDER"'))
    productionize(cfg, name="AlphaX", params=PARAMS)
    assert cfg["gsim"]["Modules"]["Alpha"]["@module"] == \
        "/mnt/storage/alphalib/alpha_src/AlphaX/AlphaX.py"


def test_module_node_selection_in_list():
    """多 Alpha 模块节点:优先 basename == <name>.py 的,不碰其它。"""
    cfg = xmltodict.parse(_ARCHIVED)
    cfg["gsim"]["Modules"]["Alpha"] = [
        {"@id": "OtherMod", "@module": "/old/Other/Other.py"},
        {"@id": "AlphaXMod", "@module": "/old/AlphaX/AlphaX.py"},
    ]
    productionize(cfg, name="AlphaX", params=PARAMS)
    nodes = cfg["gsim"]["Modules"]["Alpha"]
    assert nodes[0]["@module"] == "/old/Other/Other.py"          # 不动
    assert nodes[1]["@module"] == \
        "/mnt/storage/alphalib/alpha_src/AlphaX/AlphaX.py"


def test_single_data_item_dict_branch(tmp_path):
    """xmltodict 单 Data 项是 dict 不是 list —— 走查/削 Mod 分支别翻车。"""
    xml = _ARCHIVED
    for kill in ('\t\t<Data id="ALL_TRD" module="UmgrTrd" path=""></Data>\n',
                 '\t\t<Data id="fancy" module="Dmgrfancy" niodatapath="/cache/data/x"></Data>\n',
                 '\t\t<Data id="fivemin" module="Dmgr5m" dataPath="/home/fguo/data_local"></Data>\n',
                 '\t\t<Data id="wind" module="DmgrW" dataPath="/datasvc/rawdata_wind/HS300/"></Data>\n',
                 '\t\t<Data id="ext" module="DmgrExt" niodatapath="/somewhere/else"></Data>\n'):
        xml = xml.replace(kill, "")
    cfg = xmltodict.parse(xml)
    productionize(cfg, name="AlphaX", params=PARAMS)
    d = cfg["gsim"]["Modules"]["Data"]
    assert d["@id"] == "FULL"
    assert d["@niodatapath"] == "/nvme125/datasvc/data/cc_all/FULL"


def test_idempotent():
    """生产化两次 ≡ 一次 —— 迁移脚本可重跑、重入库归档不叠加的根基。"""
    cfg = xmltodict.parse(_ARCHIVED)
    productionize(cfg, name="AlphaX", params=PARAMS)
    once = copy.deepcopy(cfg)
    productionize(cfg, name="AlphaX", params=PARAMS)
    assert cfg == once


def test_missing_backbone_is_loud():
    """缺承重节点直接抛 —— 静默跳过 = 归档出半生产态 XML。"""
    cfg = xmltodict.parse(_ARCHIVED)
    del cfg["gsim"]["Portfolio"]
    with pytest.raises(KeyError):
        productionize(cfg, name="AlphaX", params=PARAMS)


def test_productionize_file_roundtrip(tmp_path):
    from ops.utils.xmlio import load_xml
    f = tmp_path / "Config.AlphaX.xml"
    f.write_text(_ARCHIVED)
    productionize_file(f, name="AlphaX", params=PARAMS)
    g = load_xml(f)["gsim"]
    assert g["Universe"]["@enddate"] == "TODAY"
    assert g["Constants"]["@checkpointDir"] == "/nvme125/checkpoint/AlphaX/"


def test_from_config_full_and_missing():
    import yaml

    from ops.infra.config import Config, get_project_root

    base = yaml.safe_load((get_project_root() / "config.yaml").read_text())
    raw, _, _ = Config._resolve_vars(dict(base), "server-170")
    p = ProdParams.from_config(Config(raw))
    assert p == PARAMS

    base["produce"].pop("dump_root")
    raw2, _, _ = Config._resolve_vars(dict(base), "server-170")
    with pytest.raises(ValueError, match="produce.dump_root"):
        ProdParams.from_config(Config(raw2))
