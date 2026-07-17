"""ops produce 单测(纯函数 + json 后端,无需 PG / gsim)。

覆盖:就绪三重规则与回退、缺失推导边界、XML 构造(换根/窗口/dump 开 pnl 关)、
安装语义(wanted 承载覆盖策略、半日自愈、原子无残留)、worker 三路
(ok/skipped/locked/failed)、run_produce 编排(守卫、dry-run、退出码)。
批量选集(repo.find)是 PG-only,见 test_produce_pg 组(文件末尾)。
"""
import argparse
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

import ops.services.produce.produce as produce_mod
from ops.core.dumpfiles import dump_dates
from ops.core.state import FactorRecord, FactorStatus
from ops.infra.lock import factor_lock
from ops.services.produce.dates import (
    ProduceError,
    missing_dates,
    resolve_axis,
    resolve_target,
    window_dates,
)
from ops.services.produce.produce import _install, _produce_worker, run_produce
from ops.services.produce.xml_prepare import prepare_produce_xml, rebase_niodatapath

AXIS = [20251230, 20251231, 20260105, 20260106, 20260107]
INS = ("000001", "000002")


def seed_nio(root, dates=tuple(AXIS), last_row_nan=False, all_nan=False):
    """造假数据根:raw 轴文件 + Basedata/.meta + close.npy(gsim 无 header 格式)。"""
    uni = root / "__universe"
    uni.mkdir(parents=True, exist_ok=True)
    np.array(dates, dtype=np.int64).tofile(uni / "Dates.npy")
    np.array(INS, dtype="U32").tofile(uni / "Instruments.npy")
    bd = root / "Basedata"
    bd.mkdir(exist_ok=True)
    (bd / ".meta").write_text(
        f"{dates[-1]}\ndateCapacity {len(dates)}\ninstrumentCapacity {len(INS)}\n")
    close = np.full((len(dates), len(INS)), 1.0)
    if last_row_nan:
        close[-1, :] = np.nan
    if all_nan:
        close[:, :] = np.nan
    close.tofile(bd / "close.npy")


def _axis_config(root):
    """resolve_axis 只消费两个属性,unit 测试用轻量替身即可。"""
    return SimpleNamespace(produce_nio_data_path=root,
                           produce_readiness_dirs=["Basedata"])


# ---------------------------------------------------------------------------
# dates:就绪判定 + 缺失推导
# ---------------------------------------------------------------------------

def test_window_and_missing():
    assert window_dates(AXIS, 20260101, 20260107) == [20260105, 20260106, 20260107]
    assert window_dates(AXIS, 20250101, 20251231) == [20251230, 20251231]
    # 洞天然现形(集合差)
    assert missing_dates([20260105, 20260106, 20260107],
                         {20260106}) == [20260105, 20260107]
    assert missing_dates([20260105], {20260105}) == []


def test_resolve_axis_ready(tmp_path):
    seed_nio(tmp_path)
    dates, latest = resolve_axis(_axis_config(tmp_path))
    assert dates == AXIS
    assert latest == 20260107


def test_resolve_axis_backs_off_nan_placeholder(tmp_path):
    """build_cc 末行 NaN 占位:.meta lastDate 在但没真数据 → 回退一天。"""
    seed_nio(tmp_path, last_row_nan=True)
    _, latest = resolve_axis(_axis_config(tmp_path))
    assert latest == 20260106


def test_resolve_axis_loud_when_all_placeholder(tmp_path):
    seed_nio(tmp_path, all_nan=True)
    with pytest.raises(ProduceError, match="数据未就绪"):
        resolve_axis(_axis_config(tmp_path))


def test_resolve_axis_meta_missing_is_loud(tmp_path):
    seed_nio(tmp_path)
    (tmp_path / "Basedata" / ".meta").unlink()
    with pytest.raises(FileNotFoundError):
        resolve_axis(_axis_config(tmp_path))


def test_resolve_target_rules():
    assert resolve_target(AXIS, 20260107, None) == 20260107
    assert resolve_target(AXIS, 20260107, 20260105) == 20260105
    with pytest.raises(ProduceError, match="不是交易日"):
        resolve_target(AXIS, 20260107, 20260104)
    # 就绪闸门回退后,显式要最新日必须拒绝而非静默换日
    with pytest.raises(ProduceError, match="未就绪"):
        resolve_target(AXIS, 20260106, 20260107)


# ---------------------------------------------------------------------------
# xml_prepare:换根 + 生产形态改写
# ---------------------------------------------------------------------------

def test_rebase_niodatapath():
    assert (rebase_niodatapath("/datasvc/data/cc_2025/Interval5m", "/x/cc_all")
            == "/x/cc_all/Interval5m")
    assert (rebase_niodatapath("/datasvc/data/cc/Interval5m/", "/x/cc_all")
            == "/x/cc_all/Interval5m/")
    assert rebase_niodatapath("/datasvc/data/cc_2025", "/x/cc_all") == "/x/cc_all"
    assert rebase_niodatapath("/somewhere/else", "/x/cc_all") is None


_ARCHIVED_XML = """<gsim>
\t<Constants backdays="256" niodatapath="/datasvc/data/cc_2025" checkpointDir="/old/ckpt/" checkpointDays="5"></Constants>
\t<Universe startdate="20150101" enddate="20251231"></Universe>
\t<Modules>
\t\t<Data id="ALL_TRD" module="UmgrTrd" niodatapath="/datasvc/data/cc_2025/__universe"></Data>
\t\t<Data id="ext" module="DmgrExt" niodatapath="/somewhere/else"></Data>
\t\t<Alpha id="AlphaXMod" module="/tank/vault/alphalib/alpha_src/AlphaX/AlphaX.py"></Alpha>
\t</Modules>
\t<Portfolio id="MyPort" booksize="20e6">
\t\t<Stats module="StatsSimpleV6" mode="0" dumpPnl="true" pnlDir="/tmp/alphalib/alpha_pnl"></Stats>
\t\t<Alpha id="AlphaX" module="AlphaXMod" delay="1" dumpAlphaFile="true" dumpAlphaDir="/tmp/alphalib/alpha_dump">
\t\t</Alpha>
\t</Portfolio>
</gsim>
"""


def test_prepare_produce_xml(tmp_path):
    from ops.utils.xmlio import load_xml
    workdir = tmp_path / "src" / "AlphaX"
    workdir.mkdir(parents=True)
    (workdir / "Config.AlphaX.xml").write_text(_ARCHIVED_XML)

    xml = prepare_produce_xml(
        workdir, start=20260105, end=20260107, nio_root=tmp_path / "cc_all",
        dump_root=tmp_path / "alpha", pnl_dir=tmp_path / "pnl",
        checkpoint_dir=tmp_path / "ckpt")

    gsim = load_xml(xml)["gsim"]
    assert gsim["Universe"]["@startdate"] == "20260105"
    assert gsim["Universe"]["@enddate"] == "20260107"
    assert gsim["Constants"]["@niodatapath"] == str(tmp_path / "cc_all")
    assert gsim["Constants"]["@checkpointDir"] == str(tmp_path / "ckpt") + "/"
    data = gsim["Modules"]["Data"]
    assert data[0]["@niodatapath"] == str(tmp_path / "cc_all") + "/__universe"
    assert data[1]["@niodatapath"] == "/somewhere/else"       # 非 cc 形态不动
    assert gsim["Portfolio"]["Alpha"]["@dumpAlphaFile"] == "true"
    assert gsim["Portfolio"]["Alpha"]["@dumpAlphaDir"] == str(tmp_path / "alpha")
    assert gsim["Portfolio"]["Stats"]["@dumpPnl"] == "false"
    assert gsim["Portfolio"]["Stats"]["@pnlDir"] == str(tmp_path / "pnl")


def test_prepare_produce_xml_single_data_item(tmp_path):
    """xmltodict 单元素不是 list 是 dict —— 分支别翻车。"""
    from ops.utils.xmlio import load_xml
    xml_text = _ARCHIVED_XML.replace(
        '\t\t<Data id="ext" module="DmgrExt" niodatapath="/somewhere/else"></Data>\n', "")
    workdir = tmp_path / "AlphaX"
    workdir.mkdir()
    (workdir / "Config.AlphaX.xml").write_text(xml_text)
    xml = prepare_produce_xml(
        workdir, start=20260105, end=20260105, nio_root=tmp_path / "cc_all",
        dump_root=tmp_path / "alpha", pnl_dir=tmp_path / "pnl",
        checkpoint_dir=tmp_path / "ckpt")
    data = load_xml(xml)["gsim"]["Modules"]["Data"]
    assert data["@niodatapath"] == str(tmp_path / "cc_all") + "/__universe"


# ---------------------------------------------------------------------------
# _install:覆盖策略由 wanted 承载
# ---------------------------------------------------------------------------

def _produce_fake_output(produced, dates, value=0.5):
    for d in dates:
        mdir = produced / str(d)[:4] / str(d)[4:6]
        mdir.mkdir(parents=True, exist_ok=True)
        for v in ("v1", "v2"):
            np.save(mdir / f"{d}{v}.npy", np.full(len(INS), value))


def test_install_only_wanted(tmp_path):
    produced, sidecar = tmp_path / "out", tmp_path / "dump"
    _produce_fake_output(produced, [20260105, 20260106])
    installed, nan_dates = _install(produced, sidecar, {20260105})
    assert installed == [20260105] and nan_dates == []
    assert dump_dates(sidecar, require_both=True) == {20260105}
    assert not list(sidecar.rglob("*.tmp"))                  # 无原子写残留


def test_install_never_touches_existing_outside_wanted(tmp_path):
    produced, sidecar = tmp_path / "out", tmp_path / "dump"
    _produce_fake_output(produced, [20260105, 20260106])
    old = sidecar / "2026" / "01" / "20260105v1.npy"
    old.parent.mkdir(parents=True)
    np.save(old, np.full(len(INS), 9.9))                     # 既有哨兵
    _install(produced, sidecar, {20260106})
    assert np.load(old)[0] == pytest.approx(9.9)             # 分毫未动


def test_install_overwrites_when_wanted(tmp_path):
    """--force 语义:wanted 含既有日 → 覆盖重产;半日(只有 v2)同路自愈。"""
    produced, sidecar = tmp_path / "out", tmp_path / "dump"
    _produce_fake_output(produced, [20260105], value=0.5)
    old = sidecar / "2026" / "01" / "20260105v2.npy"
    old.parent.mkdir(parents=True)
    np.save(old, np.full(len(INS), 9.9))                     # 半日残留(无 v1)
    assert dump_dates(sidecar, require_both=True) == set()   # 半日不算有
    installed, _ = _install(produced, sidecar, {20260105})
    assert installed == [20260105]
    assert np.load(old)[0] == pytest.approx(0.5)             # 残留被重产覆盖
    assert dump_dates(sidecar, require_both=True) == {20260105}


def test_install_counts_all_nan_days(tmp_path):
    produced, sidecar = tmp_path / "out", tmp_path / "dump"
    _produce_fake_output(produced, [20260105], value=np.nan)
    installed, nan_dates = _install(produced, sidecar, {20260105})
    assert installed == [20260105] and nan_dates == [20260105]
    assert dump_dates(sidecar, require_both=True) == {20260105}   # 无效日照常安装


# ---------------------------------------------------------------------------
# worker 路由 + run_produce 编排(json 后端)
# ---------------------------------------------------------------------------

@pytest.fixture
def produce_env(json_config, write_factor):
    """json 后端 produce 环境:假数据根 + alpha_src 里一个 ACTIVE 因子。"""
    import shutil

    from ops.infra.store import default_store

    cfg_path, config = json_config
    seed_nio(config.produce_nio_data_path)
    store = default_store(config)

    def _add(name, status=FactorStatus.ACTIVE):
        write_factor(config, name=name)
        shutil.move(str(config.staging / name), str(config.alpha_src / name))
        store.put(FactorRecord(
            name=name, status=status,
            updated_at="2026-07-16T00:00:00", submitted_at="2026-07-16T00:00:00",
            entered_at="2026-07-16T00:00:00" if status == FactorStatus.ACTIVE else None))

    return cfg_path, config, _add


def _fake_backtest(config, expect_nio=None):
    """替身 gsim:读 XML 的窗口与 dump 目录,把窗口内每个 AXIS 交易日的
    v1/v2 写进 dumpAlphaDir/<@id>/(模拟 gsim 自建 @id 子目录)。"""
    from ops.utils.xmlio import load_xml

    def _run(xml_file, cfg):
        gsim = load_xml(xml_file)["gsim"]
        if expect_nio is not None:
            assert gsim["Constants"]["@niodatapath"] == str(expect_nio)
        start = int(gsim["Universe"]["@startdate"])
        end = int(gsim["Universe"]["@enddate"])
        name = gsim["Portfolio"]["Alpha"]["@id"]
        produced = Path(gsim["Portfolio"]["Alpha"]["@dumpAlphaDir"]) / name
        _produce_fake_output(produced, [d for d in AXIS if start <= d <= end])
    return _run


def test_worker_ok_and_workspace_cleanup(produce_env):
    _, config, add = produce_env
    add("AlphaWbaiP1")
    fn = _fake_backtest(config, expect_nio=config.produce_nio_data_path)
    name, status, detail = _produce_worker(
        "AlphaWbaiP1", [20260105, 20260106, 20260107], config, backtest_fn=fn)
    assert status == "ok" and "+3 天" in detail
    assert dump_dates(config.alpha_dump / "AlphaWbaiP1",
                      require_both=True) == {20260105, 20260106, 20260107}
    # 成功后工作区清场
    assert not (config.produce_workspace / "src" / "AlphaWbaiP1").exists()
    assert not (config.produce_workspace / "alpha" / "AlphaWbaiP1").exists()


def test_worker_skips_non_active(produce_env):
    _, config, add = produce_env
    add("AlphaWbaiP2", status=FactorStatus.SUBMITTED)
    name, status, detail = _produce_worker(
        "AlphaWbaiP2", [20260105], config, backtest_fn=_fake_backtest(config))
    assert status == "skipped" and "submitted" in detail


def test_worker_locked(produce_env):
    _, config, add = produce_env
    add("AlphaWbaiP3")
    with factor_lock("AlphaWbaiP3", config):
        name, status, _ = _produce_worker(
            "AlphaWbaiP3", [20260105], config, backtest_fn=_fake_backtest(config))
    assert status == "locked"


def test_worker_failure_keeps_workspace(produce_env):
    _, config, add = produce_env
    add("AlphaWbaiP4")

    def _boom(xml_file, cfg):
        raise RuntimeError("gsim exploded")

    name, status, detail = _produce_worker(
        "AlphaWbaiP4", [20260105], config, backtest_fn=_boom)
    assert status == "failed" and "exploded" in detail
    # 失败残场保留供排查(下次开跑 wipe)
    assert (config.produce_workspace / "src" / "AlphaWbaiP4").exists()


def _args(cfg_path, **kw):
    base = dict(factors=[], user=None, date=None, start=None, force=False,
                dry_run=False, yes=True, workers=1, config_path=cfg_path)
    base.update(kw)
    return argparse.Namespace(**base)


def test_run_produce_end_to_end(produce_env, monkeypatch):
    cfg_path, config, add = produce_env
    add("AlphaWbaiA")
    add("AlphaWbaiB")
    # B 预先补齐 → 已最新,不应重跑
    _produce_fake_output(config.alpha_dump / "AlphaWbaiB",
                         [20260105, 20260106, 20260107], value=7.7)
    monkeypatch.setattr(produce_mod, "Runner",
                        SimpleNamespace(run_backtest=_fake_backtest(config)))

    run_produce(_args(cfg_path, factors=["AlphaWbaiA", "AlphaWbaiB"]))

    assert dump_dates(config.alpha_dump / "AlphaWbaiA",
                      require_both=True) == {20260105, 20260106, 20260107}
    # B 分毫未动(哨兵值仍在)
    b_file = config.alpha_dump / "AlphaWbaiB" / "2026" / "01" / "20260105v1.npy"
    assert np.load(b_file)[0] == pytest.approx(7.7)


def test_run_produce_dry_run_writes_nothing(produce_env, monkeypatch):
    cfg_path, config, add = produce_env
    add("AlphaWbaiD")
    monkeypatch.setattr(produce_mod, "Runner",
                        SimpleNamespace(run_backtest=_fake_backtest(config)))
    run_produce(_args(cfg_path, factors=["AlphaWbaiD"], dry_run=True))
    assert dump_dates(config.alpha_dump / "AlphaWbaiD") == set()


def test_run_produce_failure_exit_code(produce_env, monkeypatch):
    cfg_path, config, add = produce_env
    add("AlphaWbaiF")

    def _boom(xml_file, cfg):
        raise RuntimeError("boom")

    monkeypatch.setattr(produce_mod, "Runner", SimpleNamespace(run_backtest=_boom))
    with pytest.raises(SystemExit) as ei:
        run_produce(_args(cfg_path, factors=["AlphaWbaiF"]))
    assert ei.value.code == 1


@pytest.mark.parametrize("kw,msg", [
    (dict(force=True), "--force 必须显式给 --date"),
    (dict(start="20260105"), "--start 仅与 --force 连用"),
    (dict(force=True, date="20260107", start="20251230"), "早于生产起点"),
    (dict(date="20260104"), "不是交易日"),
    (dict(factors=["AlphaX"], user="wbai"), "不能同时给"),
])
def test_run_produce_guards(produce_env, capsys, kw, msg):
    cfg_path, config, add = produce_env
    with pytest.raises(SystemExit) as ei:
        run_produce(_args(cfg_path, **kw))
    assert ei.value.code == 1
    assert msg in capsys.readouterr().out


def test_run_produce_force_reproduces(produce_env, monkeypatch):
    """--force --date 单日:覆盖既有 dump(确认经 -y 跳过)。"""
    cfg_path, config, add = produce_env
    add("AlphaWbaiR")
    _produce_fake_output(config.alpha_dump / "AlphaWbaiR",
                         [20260105, 20260106, 20260107], value=7.7)
    monkeypatch.setattr(produce_mod, "Runner",
                        SimpleNamespace(run_backtest=_fake_backtest(config)))
    run_produce(_args(cfg_path, factors=["AlphaWbaiR"],
                      force=True, date="20260106"))
    d = config.alpha_dump / "AlphaWbaiR" / "2026" / "01"
    assert np.load(d / "20260106v1.npy")[0] == pytest.approx(0.5)   # 重产
    assert np.load(d / "20260105v1.npy")[0] == pytest.approx(7.7)   # 界外不动


def test_run_produce_missing_config_block(produce_env):
    cfg_path, config, add = produce_env
    base = yaml.safe_load(cfg_path.read_text())
    base.pop("produce")
    cfg_path.write_text(yaml.safe_dump(base, allow_unicode=True))
    with pytest.raises(SystemExit, match="produce"):
        run_produce(_args(cfg_path))


def test_run_produce_bulk_needs_pg(produce_env):
    """批量模式(无显式因子)在 json 后端响亮拒绝,不静默空跑。"""
    cfg_path, config, add = produce_env
    add("AlphaWbaiG")
    with pytest.raises(SystemExit, match="postgres"):
        run_produce(_args(cfg_path))


# ---------------------------------------------------------------------------
# PG 组:批量选集走 repo.find(status='active')
# ---------------------------------------------------------------------------

def test_bulk_selection_pg(test_config, seed_factor, monkeypatch):
    cfg_path, config = test_config
    seed_nio(config.produce_nio_data_path)
    seed_factor("AlphaWbaiPgA", FactorStatus.ACTIVE)
    seed_factor("AlphaWbaiPgR", FactorStatus.REJECTED)
    seed_factor("AlphaLhwPgB", FactorStatus.ACTIVE, author="lhw")

    from ops.services.produce.produce import _select_names
    assert _select_names(config, [], None) == ["AlphaLhwPgB", "AlphaWbaiPgA"]
    assert _select_names(config, [], "wbai") == ["AlphaWbaiPgA"]
    # 显式点名:非 ACTIVE 拒之门外
    assert _select_names(config, ["AlphaWbaiPgR", "AlphaWbaiPgA"], None) == [
        "AlphaWbaiPgA"]
