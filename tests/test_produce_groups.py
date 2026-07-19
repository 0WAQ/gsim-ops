"""ops produce --grouped 单测(三态:组产/单产/待产)。

json 后端 + FakeRepo(roster/单产注册内存仿)+ fake backtest,无需 PG / gsim。
覆盖:sync 组侧(静音/解静音/漂移降级转单产/不变量校验)、单产侧(离库移除/
漂移重冻结)、待产推导、准入(--single-only 点名)、运行落点与退出码。
组 XML 由真实 core/prodgroup 生成(生成路径即被测路径)。
"""
import argparse
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import xmltodict

import ops.services.produce.groups as groups_mod
from ops.core.prodgroup import GroupParams, as_list, build_group_xml
from ops.core.state import FactorRecord, FactorStatus
from ops.infra.groups.pg_store import GroupMember, ProduceGroup, ProduceSingle
from ops.services.produce.groups import (
    _admit_single,
    _run_single,
    run_produce_groups,
    sync_groups,
)
from ops.utils.xmlio import load_xml, save_xml


def _factor_cfg(name: str, delay: str = "1") -> dict:
    return xmltodict.parse(f"""<gsim>
\t<Constants backdays="256" niodatapath="/nvme125/datasvc/data/cc_all" checkpointDays="5" checkpointDir="/nvme125/checkpoint/{name}/"></Constants>
\t<Universe startdate="20110101" enddate="TODAY" secID="/datasvc/rawdata/secID" holidaysfile="/datasvc/rawdata/holidays" calendarfile="/datasvc/rawdata/wind_calendar.csv"></Universe>
\t<Modules>
\t\t<Data id="ALL_TRD" module="UmgrTrd" path=""></Data>
\t\t<Alpha id="{name}Mod" module="/mnt/storage/alphalib/alpha_src/{name}/{name}.py"></Alpha>
\t</Modules>
\t<Portfolio id="MyPort" booksize="20e6" homecurrency="CNY">
\t\t<Stats module="StatsSimpleV6" mode="0" tradePrice="close" dumpPnl="true" pnlDir="/nvme125/alpha_pnl"></Stats>
\t\t<Alpha id="{name}" module="{name}Mod" universeId="ALL_TRD" booksize="20e6" delay="{delay}" dumpAlphaFile="true" dumpAlphaDir="/nvme125/alpha_dump">
\t\t\t<Operations><Operation module="AlphaOpDecay" days="3"></Operation></Operations>
\t\t</Alpha>
\t</Portfolio>
</gsim>
""")


class _FakeRepo:
    """roster + 单产注册的内存替身(PG store 真身测试在 test_group_store_pg)。"""
    def __init__(self, active, roster, records=None, singles=None):
        # active: [(name, author, delay)];roster: {gid: (author, [(factor, muted)])}
        self._active = active
        self._roster = {g: (a, list(legs)) for g, (a, legs) in roster.items()}
        self._records = records or {}
        self._singles = dict(singles or {})      # factor -> author

    def find(self, status=None, **kw):
        return [SimpleNamespace(name=n, identity=SimpleNamespace(author=a),
                                snapshot=SimpleNamespace(delay=d))
                for n, a, d in self._active if status in (None, "active")]

    def get(self, name):
        for n, a, _ in self._active:
            if n == name:
                return SimpleNamespace(identity=SimpleNamespace(author=a))
        return None

    def record(self, name):
        return self._records.get(name)

    # -- 组 --
    def group_membership(self):
        return {f: gid for gid, (_, legs) in self._roster.items() for f, _ in legs}

    def groups(self, active_only=True):
        return [ProduceGroup(gid=gid, author=a, delay=1)
                for gid, (a, _) in sorted(self._roster.items())]

    def group_members(self, gid):
        return [GroupMember(gid=gid, factor=f, ordinal=i, muted=m)
                for i, (f, m) in enumerate(self._roster[gid][1])]

    def set_group_muted(self, gid, factors, muted):
        self._roster[gid] = (self._roster[gid][0], [
            (f, muted if f in factors else m) for f, m in self._roster[gid][1]])

    # -- 单产 --
    def admit_single(self, factor, author):
        self._singles[factor] = author

    def remove_single(self, factor):
        self._singles.pop(factor, None)

    def singles(self):
        return [ProduceSingle(factor=f, author=a)
                for f, a in sorted(self._singles.items())]


def _active_record(name):
    return FactorRecord(name=name, status=FactorStatus.ACTIVE,
                        updated_at="2026-07-19T00:00:00",
                        submitted_at="2026-07-19T00:00:00",
                        entered_at="2026-07-19T00:00:00")


def _mk_factor(config, name: str, delay: str = "1",
               code: str = "X = 1\n") -> None:
    """落一个 alpha_src 因子目录(归档生产 XML + .py)。"""
    d = config.alpha_src / name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.py").write_text(code)
    save_xml(d / f"Config.{name}.xml", _factor_cfg(name, delay))


def _mk_group(config, author: str, gid: str, members: list[str],
              code: str = "X = 1\n") -> Path:
    """用真实生成路径落一个组(group.xml + 冻结副本 + checkpoint 目录)。"""
    params = GroupParams.maybe_from_config(config)
    legs = [(n, _factor_cfg(n)) for n in members]
    res = build_group_xml(legs, params, author, gid)
    assert res.conflicts == []
    gdir = Path(params.group_dir(author, gid))
    (gdir / "checkpoint").mkdir(parents=True, exist_ok=True)
    for n in members:
        cdir = gdir / "code" / n
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / f"{n}.py").write_text(code)
    save_xml(gdir / "group.xml", res.gsim)
    return gdir


def _fake_backtest(seen: list):
    """替身 gsim:按 XML 的腿写 dump(组 sibling / 单产单 Alpha 通吃)。"""
    def _run(xml_file, cfg, timeout=None, log_path=None):
        seen.append(Path(xml_file))
        g = load_xml(Path(xml_file))["gsim"]
        alphas = g["Portfolio"]["Alpha"]
        for a in (alphas if isinstance(alphas, list) else [alphas]):
            if a.get("@dumpAlphaFile") != "true":
                continue
            root = Path(a["@dumpAlphaDir"]) / a["@id"] / "2026" / "07"
            root.mkdir(parents=True, exist_ok=True)
            np.save(root / "20260717v2.npy", np.zeros(2))
    return _run


def _args(cfg_path, **kw):
    base = dict(config_path=cfg_path, grouped=True, dry_run=False,
                sync_only=False, groups_only=False, single_only=None,
                workers=1, timeout=None)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def env(json_config, monkeypatch):
    cfg_path, config = json_config

    def _install(fake):
        monkeypatch.setattr(groups_mod, "FactorRepository", lambda cfg: fake)
    return cfg_path, config, _install


# ---------------------------------------------------------------------------
# sync:组侧
# ---------------------------------------------------------------------------

def test_sync_mutes_left_and_demotes_drift_to_single(env):
    cfg_path, config, install = env
    for n in ("AlphaA", "AlphaB", "AlphaC"):
        _mk_factor(config, n)
    _mk_group(config, "wbai", "g001", ["AlphaA", "AlphaB", "AlphaC"])
    # AlphaB 离开 ACTIVE;AlphaC 冻结副本与活代码漂移(= 重入库)
    (config.alpha_src / "AlphaC" / "AlphaC.py").write_text("X = 2\n")
    fake = _FakeRepo(
        active=[("AlphaA", "wbai", 1), ("AlphaC", "wbai", 1)],
        roster={"g001": ("wbai", [("AlphaA", False), ("AlphaB", False),
                                  ("AlphaC", False)])})
    install(fake)
    params = GroupParams.maybe_from_config(config)

    _, corrupted, pending = sync_groups(fake, params, config)

    assert corrupted == []
    members = {m.factor: m.muted for m in fake.group_members("g001")}
    assert members == {"AlphaA": False, "AlphaB": True, "AlphaC": True}
    # 漂移降级:AlphaC 组内静音 + 注册单产,单产目录三件套就位
    assert "AlphaC" in fake._singles
    sdir = Path(params.single_dir("wbai", "AlphaC"))
    assert (sdir / "AlphaC.xml").is_file()
    assert (sdir / "code" / "AlphaC.py").read_text() == "X = 2\n"   # 冻结的是新代码
    assert (sdir / "checkpoint").is_dir()
    # 单产 XML:补丁式 —— 单 Alpha 形态,落点全部指新根
    g = load_xml(sdir / "AlphaC.xml")["gsim"]
    assert g["Portfolio"]["Alpha"]["@id"] == "AlphaC"
    assert g["Portfolio"]["Alpha"]["@dumpAlphaDir"] == params.dump_root
    assert g["Constants"]["@checkpointDir"].startswith(str(sdir))
    assert pending == []


def test_sync_unmutes_returned_and_removes_from_single(env):
    cfg_path, config, install = env
    _mk_factor(config, "AlphaA")
    gdir = _mk_group(config, "wbai", "g001", ["AlphaA"])
    fake = _FakeRepo(active=[("AlphaA", "wbai", 1)],
                     roster={"g001": ("wbai", [("AlphaA", True)])},
                     singles={"AlphaA": "wbai"})
    install(fake)

    sync_groups(fake, GroupParams.maybe_from_config(config), config)

    assert fake.group_members("g001")[0].muted is False
    assert "AlphaA" not in fake._singles          # 一个因子只有一个生产之家
    cfg = load_xml(gdir / "group.xml")
    assert as_list(cfg["gsim"]["Portfolio"]["Alpha"])[0]["@dumpAlphaFile"] == "true"


def test_sync_order_mismatch_is_corrupted(env):
    cfg_path, config, install = env
    _mk_factor(config, "AlphaA")
    _mk_factor(config, "AlphaB")
    gdir = _mk_group(config, "wbai", "g001", ["AlphaA", "AlphaB"])
    cfg = load_xml(gdir / "group.xml")
    alphas = cfg["gsim"]["Portfolio"]["Alpha"]
    alphas[0], alphas[1] = alphas[1], alphas[0]
    save_xml(gdir / "group.xml", cfg)
    fake = _FakeRepo(active=[("AlphaA", "wbai", 1), ("AlphaB", "wbai", 1)],
                     roster={"g001": ("wbai", [("AlphaA", False), ("AlphaB", False)])})
    install(fake)

    _, corrupted, _ = sync_groups(fake, GroupParams.maybe_from_config(config), config)
    assert corrupted == ["g001"]


def test_pending_excludes_grouped_and_singles(env):
    cfg_path, config, install = env
    _mk_factor(config, "AlphaA")
    _mk_factor(config, "AlphaS")
    _mk_group(config, "wbai", "g001", ["AlphaA"])
    fake = _FakeRepo(
        active=[("AlphaA", "wbai", 1), ("AlphaNew", "wbai", 1),
                ("AlphaS", "wbai", 1), ("AlphaD0", "wbai", 0)],
        roster={"g001": ("wbai", [("AlphaA", False)])},
        singles={"AlphaS": "wbai"})
    install(fake)

    _, _, pending = sync_groups(fake, GroupParams.maybe_from_config(config), config)
    assert pending == ["AlphaNew"]          # 组产/单产/delay0 都不在待产


# ---------------------------------------------------------------------------
# sync:单产侧
# ---------------------------------------------------------------------------

def test_single_removed_when_leaves_active(env):
    cfg_path, config, install = env
    fake = _FakeRepo(active=[], roster={}, singles={"AlphaOld": "wbai"})
    install(fake)
    params = GroupParams.maybe_from_config(config)
    Path(params.single_dir("wbai", "AlphaOld")).mkdir(parents=True)

    sync_groups(fake, params, config)
    assert "AlphaOld" not in fake._singles


def test_single_drift_refresh_deletes_checkpoint(env):
    cfg_path, config, install = env
    _mk_factor(config, "AlphaS", code="X = 1\n")
    fake = _FakeRepo(active=[("AlphaS", "wbai", 1)], roster={})
    install(fake)
    params = GroupParams.maybe_from_config(config)
    _admit_single("AlphaS", fake, params, config)       # 按旧代码 X=1 准入
    (config.alpha_src / "AlphaS" / "AlphaS.py").write_text("X = 2\n")   # 重入库
    sdir = Path(params.single_dir("wbai", "AlphaS"))
    ck = sdir / "checkpoint" / "archive.bin"
    ck.write_text("stale")

    sync_groups(fake, params, config)

    assert not ck.exists()                              # 漂移 = 删 checkpoint 全段重跑
    assert (sdir / "code" / "AlphaS.py").read_text() == "X = 2\n"   # 重冻结新代码


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def test_run_default_groups_and_singles(env, monkeypatch):
    cfg_path, config, install = env
    _mk_factor(config, "AlphaA")
    _mk_factor(config, "AlphaS")
    gdir = _mk_group(config, "wbai", "g001", ["AlphaA"])
    fake = _FakeRepo(active=[("AlphaA", "wbai", 1), ("AlphaS", "wbai", 1)],
                     roster={"g001": ("wbai", [("AlphaA", False)])},
                     records={"AlphaS": _active_record("AlphaS")})
    install(fake)
    params = GroupParams.maybe_from_config(config)
    _admit_single("AlphaS", fake, params, config)
    seen: list = []
    monkeypatch.setattr(groups_mod, "Runner",
                        SimpleNamespace(run_backtest=_fake_backtest(seen)))

    run_produce_groups(_args(cfg_path))

    assert seen[0] == gdir / "group.xml"
    assert seen[1] == Path(params.single_dir("wbai", "AlphaS")) / "AlphaS.xml"
    assert (Path(params.dump_root) / "AlphaS" / "2026" / "07"
            / "20260717v2.npy").exists()


def test_groups_only_skips_singles(env, monkeypatch):
    cfg_path, config, install = env
    _mk_factor(config, "AlphaA")
    _mk_factor(config, "AlphaS")
    gdir = _mk_group(config, "wbai", "g001", ["AlphaA"])
    fake = _FakeRepo(active=[("AlphaA", "wbai", 1), ("AlphaS", "wbai", 1)],
                     roster={"g001": ("wbai", [("AlphaA", False)])},
                     singles={"AlphaS": "wbai"})
    install(fake)
    params = GroupParams.maybe_from_config(config)
    _admit_single("AlphaS", fake, params, config)
    seen: list = []
    monkeypatch.setattr(groups_mod, "Runner",
                        SimpleNamespace(run_backtest=_fake_backtest(seen)))

    run_produce_groups(_args(cfg_path, groups_only=True))

    assert seen == [gdir / "group.xml"]


def test_single_only_named_admits_pending(env, monkeypatch):
    """点名 pending 因子:先准入(冻结 + XML + 注册)再跑;组不碰。"""
    cfg_path, config, install = env
    _mk_factor(config, "AlphaA")
    _mk_factor(config, "AlphaNew")
    gdir = _mk_group(config, "wbai", "g001", ["AlphaA"])
    fake = _FakeRepo(
        active=[("AlphaA", "wbai", 1), ("AlphaNew", "wbai", 1)],
        roster={"g001": ("wbai", [("AlphaA", False)])},
        records={"AlphaNew": _active_record("AlphaNew")})
    install(fake)
    params = GroupParams.maybe_from_config(config)
    seen: list = []
    monkeypatch.setattr(groups_mod, "Runner",
                        SimpleNamespace(run_backtest=_fake_backtest(seen)))

    run_produce_groups(_args(cfg_path, single_only=["AlphaNew"]))

    assert "AlphaNew" in fake._singles
    sdir = Path(params.single_dir("wbai", "AlphaNew"))
    assert seen == [sdir / "AlphaNew.xml"]
    assert seen != [gdir / "group.xml"]
    with pytest.raises(SystemExit):
        run_produce_groups(_args(cfg_path, single_only=[], groups_only=True))


def test_run_failure_exit_code(env, monkeypatch):
    cfg_path, config, install = env
    _mk_factor(config, "AlphaA")
    _mk_group(config, "wbai", "g001", ["AlphaA"])
    fake = _FakeRepo(active=[("AlphaA", "wbai", 1)],
                     roster={"g001": ("wbai", [("AlphaA", False)])})
    install(fake)

    def _boom(xml_file, cfg, timeout=None, log_path=None):
        raise RuntimeError("gsim 炸了")

    monkeypatch.setattr(groups_mod, "Runner",
                        SimpleNamespace(run_backtest=_boom))
    with pytest.raises(SystemExit) as ei:
        run_produce_groups(_args(cfg_path))
    assert ei.value.code == 1


def test_precheck_auto_mutes_bad_leg(env, monkeypatch):
    cfg_path, config, install = env
    _mk_factor(config, "AlphaA")
    _mk_factor(config, "AlphaB")
    gdir = _mk_group(config, "wbai", "g001", ["AlphaA", "AlphaB"])
    (gdir / "code" / "AlphaB" / "AlphaB.py").write_text("def broken(:\n")
    fake = _FakeRepo(active=[("AlphaA", "wbai", 1), ("AlphaB", "wbai", 1)],
                     roster={"g001": ("wbai", [("AlphaA", False), ("AlphaB", False)])})
    install(fake)
    seen: list = []
    monkeypatch.setattr(groups_mod, "Runner",
                        SimpleNamespace(run_backtest=_fake_backtest(seen)))

    run_produce_groups(_args(cfg_path))

    members = {m.factor: m.muted for m in fake.group_members("g001")}
    assert members == {"AlphaA": False, "AlphaB": True}
    assert seen == [gdir / "group.xml"]                    # 坏腿静音后组照跑


def test_dry_run_never_runs_gsim(env, monkeypatch):
    cfg_path, config, install = env
    _mk_factor(config, "AlphaA")
    _mk_group(config, "wbai", "g001", ["AlphaA"])
    fake = _FakeRepo(active=[("AlphaA", "wbai", 1)],
                     roster={"g001": ("wbai", [("AlphaA", False)])})
    install(fake)

    def _boom(xml_file, cfg, timeout=None, log_path=None):
        raise AssertionError("dry-run 不得跑 gsim")

    monkeypatch.setattr(groups_mod, "Runner",
                        SimpleNamespace(run_backtest=_boom))
    run_produce_groups(_args(cfg_path, dry_run=True))
    run_produce_groups(_args(cfg_path, sync_only=True))


def test_run_single_xml_is_patched_to_new_roots(env, monkeypatch):
    """单产 XML 是补丁式:单 Alpha 形态保留,checkpoint/dump/pnl 全指新根。"""
    cfg_path, config, install = env
    _mk_factor(config, "AlphaS")
    fake = _FakeRepo(active=[("AlphaS", "wbai", 1)], roster={},
                     records={"AlphaS": _active_record("AlphaS")})
    install(fake)
    params = GroupParams.maybe_from_config(config)
    _admit_single("AlphaS", fake, params, config)
    seen: list = []

    def _spy(xml_file, cfg, timeout=None, log_path=None):
        g = load_xml(Path(xml_file))["gsim"]
        seen.append(g)

    monkeypatch.setattr(groups_mod, "Runner", SimpleNamespace(run_backtest=_spy))
    name, status, _ = _run_single("AlphaS", "wbai", config)

    assert status == "ok" or name == "AlphaS"
    g = seen[0]
    assert not isinstance(g["Portfolio"]["Alpha"], list)     # 单 Alpha 形态
    assert g["Constants"]["@checkpointDir"].endswith(
        "single/wbai/delay1/AlphaS/checkpoint/")
    assert g["Portfolio"]["Alpha"]["@dumpAlphaDir"] == params.dump_root
    assert g["Portfolio"]["Stats"]["@pnlDir"] == params.pnl_root
    assert g["Modules"]["Alpha"]["@module"].startswith(
        str(Path(params.root) / "single"))
