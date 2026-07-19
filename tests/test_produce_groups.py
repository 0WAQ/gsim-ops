"""ops produce --grouped 单测:json 后端 + FakeRepo(组 roster PG-only,内存仿)
+ fake backtest,无需 PG / gsim。

覆盖:sync 静音/解静音/漂移/不变量校验、pending 归类、pre-check 自动静音、
组与 pending 的运行与落点、失败退出码、dry-run。组 XML 由真实
core/prodgroup.build_group_xml 生成(生成路径即被测路径)。
"""
import argparse
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import xmltodict

import ops.services.produce.groups as groups_mod
from ops.core.prodgroup import GroupParams, as_list, build_group_xml, group_legs
from ops.core.state import FactorRecord, FactorStatus
from ops.infra.groups.pg_store import GroupMember, ProduceGroup
from ops.services.produce.groups import _run_pending, run_produce_groups, sync_groups
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
    """组 roster 的内存替身(PG store 的真身测试在 test_repository/pg 系)。"""
    def __init__(self, active, roster, records=None):
        # active: [(name, author, delay)];roster: {gid: (author, [(factor, muted)])}
        self._active = active
        self._roster = {g: (a, list(legs)) for g, (a, legs) in roster.items()}
        self._records = records or {}

    def find(self, status=None, **kw):
        return [SimpleNamespace(name=n, identity=SimpleNamespace(author=a),
                                snapshot=SimpleNamespace(delay=d))
                for n, a, d in self._active if status in (None, "active")]

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

    def record(self, name):
        return self._records.get(name)


def _active_record(name):
    return FactorRecord(name=name, status=FactorStatus.ACTIVE,
                        updated_at="2026-07-18T00:00:00",
                        submitted_at="2026-07-18T00:00:00",
                        entered_at="2026-07-18T00:00:00")


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
    """替身 gsim:组 XML 按腿写 dump(sibling 形态,per-leg 落盘)。"""
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
                sync_only=False, skip_pending=False, workers=1, timeout=None)
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def env(json_config, monkeypatch):
    cfg_path, config = json_config

    def _install(fake):
        monkeypatch.setattr(groups_mod, "FactorRepository", lambda cfg: fake)
    return cfg_path, config, _install


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------

def test_sync_mutes_left_active_and_code_drift(env):
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

    notes, corrupted, pending = sync_groups(fake, GroupParams.maybe_from_config(config),
                                            config)

    assert corrupted == []
    members = {m.factor: m.muted for m in fake.group_members("g001")}
    assert members == {"AlphaA": False, "AlphaB": True, "AlphaC": True}
    cfg = load_xml(Path(GroupParams.maybe_from_config(config)
                        .group_dir("wbai", "g001")) / "group.xml")
    assert group_legs(cfg) == ["AlphaA", "AlphaB", "AlphaC"]     # 序不动
    flags = {a["@id"]: a["@dumpAlphaFile"]
             for a in cfg["gsim"]["Portfolio"]["Alpha"]}
    assert flags == {"AlphaA": "true", "AlphaB": "false", "AlphaC": "false"}
    assert pending == []
    assert any("静音" in n for n in notes)


def test_sync_unmutes_returned_factor(env):
    cfg_path, config, install = env
    _mk_factor(config, "AlphaA")
    _mk_group(config, "wbai", "g001", ["AlphaA"])
    # 同一副本被换掉 → 组里改回与 alpha_src 一致(模拟回库且代码未变)
    gdir = Path(GroupParams.maybe_from_config(config).group_dir("wbai", "g001"))
    (gdir / "code" / "AlphaA" / "AlphaA.py").write_text("X = 1\n")
    fake = _FakeRepo(active=[("AlphaA", "wbai", 1)],
                     roster={"g001": ("wbai", [("AlphaA", True)])})
    install(fake)

    sync_groups(fake, GroupParams.maybe_from_config(config), config)

    assert fake.group_members("g001")[0].muted is False
    cfg = load_xml(gdir / "group.xml")
    alphas = as_list(cfg["gsim"]["Portfolio"]["Alpha"])
    assert alphas[0]["@dumpAlphaFile"] == "true"


def test_sync_order_mismatch_is_corrupted(env):
    cfg_path, config, install = env
    _mk_factor(config, "AlphaA")
    _mk_factor(config, "AlphaB")
    gdir = _mk_group(config, "wbai", "g001", ["AlphaA", "AlphaB"])
    # 手改 XML 换腿序 → 与 DB ordinal 不一致 = 现场被改过
    cfg = load_xml(gdir / "group.xml")
    alphas = cfg["gsim"]["Portfolio"]["Alpha"]
    alphas[0], alphas[1] = alphas[1], alphas[0]
    save_xml(gdir / "group.xml", cfg)
    fake = _FakeRepo(active=[("AlphaA", "wbai", 1), ("AlphaB", "wbai", 1)],
                     roster={"g001": ("wbai", [("AlphaA", False), ("AlphaB", False)])})
    install(fake)

    _, corrupted, _ = sync_groups(fake, GroupParams.maybe_from_config(config), config)
    assert corrupted == ["g001"]


def test_pending_only_new_delay1(env):
    cfg_path, config, install = env
    _mk_factor(config, "AlphaA")
    _mk_group(config, "wbai", "g001", ["AlphaA"])
    fake = _FakeRepo(
        active=[("AlphaA", "wbai", 1), ("AlphaNew", "wbai", 1),
                ("AlphaD0", "wbai", 0)],                      # delay0 不进 pending
        roster={"g001": ("wbai", [("AlphaA", False)])})
    install(fake)

    _, _, pending = sync_groups(fake, GroupParams.maybe_from_config(config), config)
    assert pending == ["AlphaNew"]


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def test_run_groups_and_pending_end_to_end(env, monkeypatch):
    cfg_path, config, install = env
    for n in ("AlphaA", "AlphaB", "AlphaNew"):
        _mk_factor(config, n)
    gdir = _mk_group(config, "wbai", "g001", ["AlphaA", "AlphaB"])
    fake = _FakeRepo(
        active=[("AlphaA", "wbai", 1), ("AlphaB", "wbai", 1), ("AlphaNew", "wbai", 1)],
        roster={"g001": ("wbai", [("AlphaA", False), ("AlphaB", False)])},
        records={"AlphaNew": _active_record("AlphaNew")})
    install(fake)
    seen: list = []
    monkeypatch.setattr(groups_mod, "Runner",
                        SimpleNamespace(run_backtest=_fake_backtest(seen)))

    run_produce_groups(_args(cfg_path))

    assert seen[0] == gdir / "group.xml"                    # 组跑的是组 XML 本尊
    assert seen[1] != gdir / "group.xml"                    # pending 跑的是临时副本
    params = GroupParams.maybe_from_config(config)
    assert (Path(params.dump_root) / "AlphaA" / "2026" / "07"
            / "20260717v2.npy").exists()
    assert (Path(params.dump_root) / "AlphaNew" / "2026" / "07"
            / "20260717v2.npy").exists()
    # pending 的 checkpoint 落 pending 根,不碰旧 dataset 侧
    assert (Path(params.pending_checkpoint_root) / "AlphaNew").is_dir()


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
    cfg = load_xml(gdir / "group.xml")
    flags = {a["@id"]: a["@dumpAlphaFile"]
             for a in cfg["gsim"]["Portfolio"]["Alpha"]}
    assert flags == {"AlphaA": "true", "AlphaB": "false"}
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


def test_skip_pending_runs_groups_only(env, monkeypatch):
    """试点口径:--skip-pending 只跑已封的组,pending 一个不碰。"""
    cfg_path, config, install = env
    _mk_factor(config, "AlphaA")
    _mk_factor(config, "AlphaNew")
    gdir = _mk_group(config, "wbai", "g001", ["AlphaA"])
    fake = _FakeRepo(
        active=[("AlphaA", "wbai", 1), ("AlphaNew", "wbai", 1)],
        roster={"g001": ("wbai", [("AlphaA", False)])},
        records={"AlphaNew": _active_record("AlphaNew")})
    install(fake)
    seen: list = []
    monkeypatch.setattr(groups_mod, "Runner",
                        SimpleNamespace(run_backtest=_fake_backtest(seen)))

    run_produce_groups(_args(cfg_path, skip_pending=True))

    assert seen == [gdir / "group.xml"]


def test_pending_worker_rewrites_roots_to_new_production(env, monkeypatch):
    """pending 的归档 XML 指旧 dataset,临时副本必须把三根改指新根。"""
    cfg_path, config, install = env
    _mk_factor(config, "AlphaNew")
    fake = _FakeRepo(active=[("AlphaNew", "wbai", 1)], roster={},
                     records={"AlphaNew": _active_record("AlphaNew")})
    install(fake)
    seen: list = []

    def _spy(xml_file, cfg, timeout=None, log_path=None):
        g = load_xml(Path(xml_file))["gsim"]
        seen.append((g["Constants"]["@checkpointDir"],
                     g["Portfolio"]["Alpha"]["@dumpAlphaDir"],
                     g["Portfolio"]["Stats"]["@pnlDir"]))

    monkeypatch.setattr(groups_mod, "Runner", SimpleNamespace(run_backtest=_spy))
    params = GroupParams.maybe_from_config(config)

    name, status, _ = _run_pending("AlphaNew", config)

    assert status == "ok" or name == "AlphaNew"
    ckdir, dumpdir, pnldir = seen[0]
    assert ckdir.startswith(params.pending_checkpoint_root)
    assert dumpdir == params.dump_root and pnldir == params.pnl_root
    assert "/nvme125/alpha_dump" not in dumpdir          # 绝不落旧 dataset
