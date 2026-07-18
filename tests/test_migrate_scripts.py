"""批 D 脚本单测:migrate_prod_xml(存量迁移)+ audit_combo_legs(接管闸门)。

迁移的规则正确性在 test_prodxml,此处只验脚本层:dry-run 零改动、apply
备份+落盘、重跑幂等(unchanged)、闸门差集与退出码。
"""
import importlib.util
import shutil
from pathlib import Path

import pytest

from ops.core.prodxml import ProdParams
from ops.infra.repository import FactorRepository
from ops.utils.xmlio import load_xml


def _load_script(name: str):
    path = Path(__file__).resolve().parent.parent / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# migrate_prod_xml
# ---------------------------------------------------------------------------

@pytest.fixture
def legacy_and_migrated(json_config, write_factor):
    """alpha_src 两态并存:AlphaWbaiLegM 拆雷/模板态(未生产化),
    AlphaWbaiDone 已生产化(经 repo.archive)。"""
    _, config = json_config
    d = write_factor(config, name="AlphaWbaiLegM")
    shutil.move(str(d), str(config.alpha_src / "AlphaWbaiLegM"))

    d2 = write_factor(config, name="AlphaWbaiDone")
    dump = config.alpha_path / "AlphaWbaiDone"
    dump.mkdir(parents=True)
    pnl = config.pnl_path / "AlphaWbaiDone"
    pnl.write_text("pnl")
    FactorRepository(config).archive("AlphaWbaiDone", src_dir=d2, dump_dir=dump,
                                     pnl_file=pnl, discovery_method="manual")
    return config


def test_migrate_one_dry_run_changes_nothing(legacy_and_migrated):
    mig = _load_script("migrate_prod_xml")
    config = legacy_and_migrated
    params = ProdParams.from_config(config)
    xml = config.alpha_src / "AlphaWbaiLegM" / "Config.AlphaWbaiLegM.xml"
    before = xml.read_text()

    status, diffs = mig.migrate_one(xml, "AlphaWbaiLegM", params,
                                    apply=False, backup_dir=None)
    assert status == "changed" and diffs           # 报告有字段
    assert xml.read_text() == before               # 盘面零改动


def test_migrate_one_apply_backup_and_idempotent(legacy_and_migrated, tmp_path):
    mig = _load_script("migrate_prod_xml")
    config = legacy_and_migrated
    params = ProdParams.from_config(config)
    xml = config.alpha_src / "AlphaWbaiLegM" / "Config.AlphaWbaiLegM.xml"
    before = xml.read_text()
    bak = tmp_path / "bak"

    status, diffs = mig.migrate_one(xml, "AlphaWbaiLegM", params,
                                    apply=True, backup_dir=bak)
    assert status == "changed"
    assert (bak / "AlphaWbaiLegM" / xml.name).read_text() == before   # 原样备份
    g = load_xml(xml)["gsim"]
    assert g["Universe"]["@enddate"] == "TODAY"
    assert g["Portfolio"]["Alpha"]["@dumpAlphaDir"] == str(config.produce_dump_root)

    # 重跑幂等:已生产态 → unchanged,零写盘
    status2, diffs2 = mig.migrate_one(xml, "AlphaWbaiLegM", params,
                                      apply=True, backup_dir=bak)
    assert (status2, diffs2) == ("unchanged", [])


def test_migrate_already_production_reports_unchanged(legacy_and_migrated):
    mig = _load_script("migrate_prod_xml")
    config = legacy_and_migrated
    params = ProdParams.from_config(config)
    xml = config.alpha_src / "AlphaWbaiDone" / "Config.AlphaWbaiDone.xml"
    status, diffs = mig.migrate_one(xml, "AlphaWbaiDone", params,
                                    apply=False, backup_dir=None)
    assert (status, diffs) == ("unchanged", [])


def test_migrate_main_dry_run(legacy_and_migrated, json_config, tmp_path,
                              monkeypatch, capsys):
    import sys
    mig = _load_script("migrate_prod_xml")
    cfg_path, _ = json_config
    report = tmp_path / "report.txt"
    monkeypatch.setattr(sys, "argv", ["migrate_prod_xml.py", "-c", str(cfg_path),
                                      "--report", str(report)])
    assert mig.main() == 0
    out = capsys.readouterr().out
    assert "将改/已改 1" in out and "已生产态 1" in out
    assert "AlphaWbaiLegM" in report.read_text()


# ---------------------------------------------------------------------------
# audit_combo_legs
# ---------------------------------------------------------------------------

_COMBO_XML = """<gsim>
\t<Constants backdays="256" niodatapath="/nvme125/datasvc/data/cc"></Constants>
\t<Universe startdate="20200101" enddate="TODAY"></Universe>
\t<Portfolio id="P" booksize="20e6">
\t\t<Stats module="StatsSimpleV6" mode="0" dumpPnl="true" pnlDir="/x"></Stats>
\t\t<Alphas id="lhw" combo="Combo_bj202" dumpAlphaDir="/nvme125/combo/combo_dump/lhw/">
\t\t\t<Alpha id="AlphaA" module="AlphaLoad" alphaDir="/nvme125/alpha_dump/" ver="v2"></Alpha>
\t\t\t<Alpha id="AlphaB" module="AlphaLoad" alphaDir="/nvme125/alpha_dump/" ver="v2"></Alpha>
\t\t\t<Alpha id="AlphaC" module="AlphaLoad" alphaDir="/nvme125/alpha_dump/" ver="v2"></Alpha>
\t\t\t<Alpha id="lhw" module="AlphaLoad" alphaDir="/nvme125/combo/combo_dump/lhw" ver="v2"></Alpha>
\t\t</Alphas>
\t</Portfolio>
</gsim>
"""


def test_combo_legs_extraction_and_gate(tmp_path, monkeypatch, capsys):
    import sys
    audit = _load_script("audit_combo_legs")
    xml = tmp_path / "mode0.xml"
    xml.write_text(_COMBO_XML)

    # 腿抽取:只认 alphaDir 含 alpha_dump 的 AlphaLoad(combo_dump 的读入不算腿)
    assert audit.combo_legs(xml, "alpha_dump") == ["AlphaA", "AlphaB", "AlphaC"]

    active = tmp_path / "active.txt"
    active.write_text("AlphaA\nAlphaB\n")
    monkeypatch.setattr(sys, "argv", ["audit_combo_legs.py", str(xml),
                                      "--active-file", str(active)])
    assert audit.main() == 1                       # C 不在 ACTIVE → 闸门不过
    assert "AlphaC" in capsys.readouterr().out

    active.write_text("AlphaA\nAlphaB\nAlphaC\n")
    monkeypatch.setattr(sys, "argv", ["audit_combo_legs.py", str(xml),
                                      "--active-file", str(active)])
    assert audit.main() == 0                       # 齐 → 通过


# ---------------------------------------------------------------------------
# produce_shadow_diff
# ---------------------------------------------------------------------------

def test_shadow_xml_redirects_only_roots(legacy_and_migrated, tmp_path):
    """影子副本:三根重定向 scratch + enddate 钉死,规则其余与生产完全一致;
    alpha_src 原件零改动。"""
    shadow = _load_script("produce_shadow_diff")
    config = legacy_and_migrated
    scratch = tmp_path / "scratch"
    archived = config.alpha_src / "AlphaWbaiDone" / "Config.AlphaWbaiDone.xml"
    before = archived.read_text()

    params = shadow.shadow_params(config, scratch, "20260714")
    out = shadow.prepare_shadow_xml(config, "AlphaWbaiDone", params, scratch)

    g = load_xml(out)["gsim"]
    assert g["Universe"]["@enddate"] == "20260714"
    assert g["Universe"]["@startdate"] == "20110101"          # 生产规则不动
    assert g["Portfolio"]["Alpha"]["@dumpAlphaDir"] == str(scratch / "alpha_dump")
    assert g["Constants"]["@checkpointDir"] == \
        f"{scratch}/checkpoint/AlphaWbaiDone/"
    assert archived.read_text() == before                     # 原件分毫未动


def test_shadow_diff_factor_buckets(tmp_path):
    import numpy as np
    shadow = _load_script("produce_shadow_diff")
    sd = tmp_path / "shadow" / "alpha_dump"
    ds = tmp_path / "dataset"

    def put(root, date, ver, arr):
        p = root / "AlphaX" / str(date)[:4] / str(date)[4:6] / f"{date}{ver}.npy"
        p.parent.mkdir(parents=True, exist_ok=True)
        np.save(p, arr)

    put(sd, 20260701, "v2", np.array([1.0, 2.0]))     # byte-equal
    put(ds, 20260701, "v2", np.array([1.0, 2.0]))
    put(sd, 20260702, "v2", np.array([1.0, 2.0]))     # drift
    put(ds, 20260702, "v2", np.array([1.0, 9.0]))
    put(sd, 20260703, "v2", np.array([1.0]))          # missing-in-dataset

    counts, details = shadow.diff_factor("AlphaX", sd, ds)
    assert counts == {"byte": 1, "atol": 0, "drift": 1, "missing": 1}
    assert any("DRIFT" in d for d in details)
    assert any("MISSING" in d for d in details)


def test_shadow_run_one_premakes_checkpoint_dir(legacy_and_migrated, tmp_path,
                                                monkeypatch, json_config):
    """gsim checkpoint.save 不自建目录(170 实测):run_one 跑测前必须预建
    per-factor checkpoint 目录。"""
    shadow = _load_script("produce_shadow_diff")
    cfg_path, _ = json_config
    config = legacy_and_migrated
    scratch = tmp_path / "scratch"
    seen = {}

    def _fake(xml_file, cfg):
        seen["ck_exists"] = (scratch / "checkpoint" / "AlphaWbaiDone").is_dir()

    monkeypatch.setattr(shadow.Runner, "run_backtest", staticmethod(_fake))
    name, err = shadow.run_one("AlphaWbaiDone", cfg_path, str(scratch), "20260714")
    assert err == ""
    assert seen["ck_exists"] is True          # 跑测那一刻目录已在
