"""ops produce(v3 薄驱动)单测:json 后端 + fake backtest,无需 PG / gsim。

覆盖:sync 停线/新线语义、未迁移守卫、worker 四路(ok/locked/skipped/
unmigrated)、--force 删 checkpoint、--enddate 临时副本、编排守卫与退出码。
归档生产化本身在 test_prodxml / test_repository,此处只消费。
"""
import argparse
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import ops.services.produce.produce as produce_mod
from ops.core.state import FactorRecord, FactorStatus
from ops.infra.lock import factor_lock
from ops.infra.repository import FactorRepository
from ops.services.produce.produce import _produce_worker, run_produce, sync_lines
from ops.utils.xmlio import load_xml


@pytest.fixture
def produce_env(json_config, write_factor):
    """经真实 repo.archive 入库(XML 已生产化到隔离产线根)+ json state。"""
    from ops.infra.store import default_store

    cfg_path, config = json_config
    repo = FactorRepository(config)
    store = default_store(config)

    def _add(name, status=FactorStatus.ACTIVE):
        d = write_factor(config, name=name)
        dump = config.alpha_path / name
        dump.mkdir(parents=True, exist_ok=True)
        pnl = config.pnl_path / name
        pnl.write_text("pnl")
        repo.archive(name, src_dir=d, dump_dir=dump, pnl_file=pnl,
                     discovery_method="manual")
        store.put(FactorRecord(
            name=name, status=status,
            updated_at="2026-07-16T00:00:00", submitted_at="2026-07-16T00:00:00",
            entered_at="2026-07-16T00:00:00" if status == FactorStatus.ACTIVE else None))

    return cfg_path, config, _add


def _fake_backtest(seen: list, dates=(20260717,)):
    """替身 gsim:记录被跑的 XML 路径,把 dump 写进 XML 声明的产线 dump 根。"""
    def _run(xml_file, cfg):
        seen.append(Path(xml_file))
        g = load_xml(xml_file)["gsim"]
        name = g["Portfolio"]["Alpha"]["@id"]
        root = Path(g["Portfolio"]["Alpha"]["@dumpAlphaDir"]) / name
        for d in dates:
            mdir = root / str(d)[:4] / str(d)[4:6]
            mdir.mkdir(parents=True, exist_ok=True)
            for v in ("v1", "v2"):
                np.save(mdir / f"{d}{v}.npy", np.zeros(2))
    return _run


def _args(cfg_path, **kw):
    base = dict(factors=[], user=None, dry_run=False, sync_only=False,
                force=False, enddate=None, yes=True, workers=1,
                config_path=cfg_path)
    base.update(kw)
    return argparse.Namespace(**base)


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------

def test_sync_lines_retire_and_fresh(tmp_path):
    ck = tmp_path / "checkpoint"
    for d in ("AlphaOld", "AlphaKeep", "combo_x"):
        (ck / d).mkdir(parents=True)
        (ck / d / "archive.bin").write_text("x")

    retired, fresh = sync_lines({"AlphaKeep", "AlphaNew"}, ck)

    assert retired == ["AlphaOld"]
    assert (ck / ".retired" / "AlphaOld" / "archive.bin").exists()
    assert not (ck / "AlphaOld").exists()
    assert (ck / "AlphaKeep").exists()
    assert (ck / "combo_x").exists()          # 非 Alpha* 目录一概不碰
    assert fresh == ["AlphaNew"]              # 新线零构建,只报告


def test_sync_lines_missing_root(tmp_path):
    assert sync_lines({"AlphaX"}, tmp_path / "nope") == ([], ["AlphaX"])


# ---------------------------------------------------------------------------
# worker 四路
# ---------------------------------------------------------------------------

def test_worker_ok_runs_archived_xml(produce_env):
    _, config, add = produce_env
    add("AlphaWbaiP1")
    seen: list = []
    name, status, detail = _produce_worker(
        "AlphaWbaiP1", config, backtest_fn=_fake_backtest(seen))
    assert status == "ok" and "20260717" in detail
    # 跑的就是 alpha_src 归档 XML 本尊(零副本零改写)
    assert seen == [config.alpha_src / "AlphaWbaiP1" / "Config.AlphaWbaiP1.xml"]
    # checkpoint per-factor 目录已预建(gsim save 不自建,新线首跑必须有)
    assert (config.produce_checkpoint_root / "AlphaWbaiP1").is_dir()


def test_worker_unmigrated_guard(produce_env, write_factor):
    """未生产化的归档 XML(拆雷态/存量未迁移)拒跑 —— 输出会静默丢失。"""
    import shutil

    from ops.infra.store import default_store

    _, config, _ = produce_env
    d = write_factor(config, name="AlphaWbaiLegacy")   # 模板态 XML,未经归档
    shutil.move(str(d), str(config.alpha_src / "AlphaWbaiLegacy"))
    default_store(config).put(FactorRecord(
        name="AlphaWbaiLegacy", status=FactorStatus.ACTIVE,
        updated_at="2026-07-16T00:00:00", submitted_at="2026-07-16T00:00:00",
        entered_at="2026-07-16T00:00:00"))

    name, status, detail = _produce_worker(
        "AlphaWbaiLegacy", config, backtest_fn=_fake_backtest([]))
    assert status == "unmigrated" and "migrate_prod_xml" in detail


def test_worker_skips_non_active(produce_env):
    _, config, add = produce_env
    add("AlphaWbaiP2", status=FactorStatus.SUBMITTED)
    name, status, detail = _produce_worker(
        "AlphaWbaiP2", config, backtest_fn=_fake_backtest([]))
    assert status == "skipped" and "submitted" in detail


def test_worker_locked(produce_env):
    _, config, add = produce_env
    add("AlphaWbaiP3")
    with factor_lock("AlphaWbaiP3", config):
        name, status, _ = _produce_worker(
            "AlphaWbaiP3", config, backtest_fn=_fake_backtest([]))
    assert status == "locked"


def test_worker_force_removes_checkpoint(produce_env):
    _, config, add = produce_env
    add("AlphaWbaiP4")
    ck = config.produce_checkpoint_root / "AlphaWbaiP4"
    ck.mkdir(parents=True)
    (ck / "archive.bin").write_text("stale")

    name, status, _ = _produce_worker(
        "AlphaWbaiP4", config, force=True, backtest_fn=_fake_backtest([]))
    assert status == "ok"
    # 全段重跑 = 旧存档已清;目录本身重建为空(gsim save 不自建目录,须预建)
    assert not (ck / "archive.bin").exists()
    assert ck.is_dir() and not list(ck.iterdir())


def test_worker_enddate_uses_temp_copy(produce_env):
    """钉死日重算:临时副本改 enddate + 一次性 checkpoint,生产 XML 分毫不动。"""
    _, config, add = produce_env
    add("AlphaWbaiP5")
    archived = config.alpha_src / "AlphaWbaiP5" / "Config.AlphaWbaiP5.xml"
    before = archived.read_text()
    seen: list = []

    def _spy(xml_file, cfg):
        g = load_xml(Path(xml_file))["gsim"]
        seen.append((Path(xml_file), g["Universe"]["@enddate"],
                     g["Constants"]["@checkpointDir"]))

    name, status, _ = _produce_worker(
        "AlphaWbaiP5", config, enddate="20251231", backtest_fn=_spy)
    assert status == "ok"
    tmp_xml, enddate, ckdir = seen[0]
    assert tmp_xml != archived                          # 副本,非本尊
    assert enddate == "20251231"
    assert str(config.produce_checkpoint_root) not in ckdir   # 不碰生产 checkpoint
    assert archived.read_text() == before               # 生产 XML 原样


# ---------------------------------------------------------------------------
# run_produce 编排
# ---------------------------------------------------------------------------

def test_run_produce_end_to_end_with_sync(produce_env, monkeypatch):
    cfg_path, config, add = produce_env
    add("AlphaWbaiA")
    add("AlphaWbaiB")
    # 离库残线:checkpoint 有而 ACTIVE 无 → 停线归 .retired
    gone = config.produce_checkpoint_root / "AlphaWbaiGone"
    gone.mkdir(parents=True)
    seen: list = []
    monkeypatch.setattr(produce_mod, "Runner",
                        SimpleNamespace(run_backtest=_fake_backtest(seen)))

    run_produce(_args(cfg_path))

    assert len(seen) == 2                     # A/B 各跑一次归档 XML
    assert not gone.exists()
    assert (config.produce_checkpoint_root / ".retired" / "AlphaWbaiGone").exists()
    # dump 落产线 dataset(非 alphalib sidecar)
    assert (config.produce_dump_root / "AlphaWbaiA" / "2026" / "07"
            / "20260717v2.npy").exists()


def test_run_produce_dry_run_and_sync_only(produce_env, monkeypatch, capsys):
    cfg_path, config, add = produce_env
    add("AlphaWbaiD")

    def _boom(xml_file, cfg):
        raise AssertionError("dry-run/sync-only 不得跑 gsim")

    monkeypatch.setattr(produce_mod, "Runner", SimpleNamespace(run_backtest=_boom))
    run_produce(_args(cfg_path, dry_run=True))
    out = capsys.readouterr().out
    assert "生产态" in out and "新线" in out

    run_produce(_args(cfg_path, sync_only=True))


def test_run_produce_targeted_skips_retire(produce_env, monkeypatch):
    """定向模式不做停线对账 —— 定向跑不该有全局副作用。"""
    cfg_path, config, add = produce_env
    add("AlphaWbaiT")
    gone = config.produce_checkpoint_root / "AlphaWbaiGone2"
    gone.mkdir(parents=True)
    monkeypatch.setattr(produce_mod, "Runner",
                        SimpleNamespace(run_backtest=_fake_backtest([])))

    run_produce(_args(cfg_path, factors=["AlphaWbaiT"]))
    assert gone.exists()                      # 未被停线


def test_run_produce_unmigrated_exit_code(produce_env, write_factor):
    import shutil

    from ops.infra.store import default_store

    cfg_path, config, _ = produce_env
    d = write_factor(config, name="AlphaWbaiLeg2")
    shutil.move(str(d), str(config.alpha_src / "AlphaWbaiLeg2"))
    default_store(config).put(FactorRecord(
        name="AlphaWbaiLeg2", status=FactorStatus.ACTIVE,
        updated_at="2026-07-16T00:00:00", submitted_at="2026-07-16T00:00:00",
        entered_at="2026-07-16T00:00:00"))

    with pytest.raises(SystemExit) as ei:
        run_produce(_args(cfg_path, factors=["AlphaWbaiLeg2"]))
    assert ei.value.code == 1


@pytest.mark.parametrize("kw", [
    dict(factors=["AlphaX"], user="wbai"),    # 语义歧义
    dict(force=True),                         # force 无界作用域
    dict(enddate="2026-07"),                  # enddate 格式
])
def test_run_produce_guards(produce_env, kw):
    cfg_path, _, _ = produce_env
    with pytest.raises(SystemExit) as ei:
        run_produce(_args(cfg_path, **kw))
    assert ei.value.code == 1


def test_run_produce_missing_block(produce_env, tmp_path):
    import yaml

    cfg_path, _, _ = produce_env
    base = yaml.safe_load(cfg_path.read_text())
    base.pop("produce")
    bare = tmp_path / "bare.yaml"
    bare.write_text(yaml.safe_dump(base, allow_unicode=True))
    with pytest.raises(SystemExit, match="produce"):
        run_produce(_args(bare))
