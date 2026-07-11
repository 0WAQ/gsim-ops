"""ops setup 引擎测试(无需 PG / 不碰系统:GROUPS 清空防 groupadd 副作用,
legacy_link / mounts 注入 tmp)。

钉住的行为:
- 应然布局全绿(FAIL 级项零失败;PG/lock 在 json 后端 skip)
- apply 补建缺失(池目录 / dump 软链 + sidecar)且幂等(二跑 fixed=False)
- --check(apply=False)零写
- 存在但指错的软链:只报告,apply 不动它(补建铁律)
- Config hosts 合并层优先级:OPS_* env > hosts[hostname] > vars
"""
from pathlib import Path

import pytest

from ops.infra.config import Config
from ops.services.setup import has_failures, run_setup
from ops.services.setup.checks import Ctx


@pytest.fixture(autouse=True)
def _no_system_groups(monkeypatch):
    """测试环境不建真实系统组(groupadd 是容器级副作用)。"""
    monkeypatch.setattr("ops.services.setup.checks.GROUPS", {})


class FakeConfig:
    def __init__(self, tmp: Path):
        root = tmp / "alphalib"
        gsim = tmp / "gsim"
        self.alpha_src = root / "alpha_src"
        self.alpha_pnl = root / "alpha_pnl"
        self.alpha_feature = root / "alpha_feature"
        self.alpha_dump = root / "alpha_dump"
        self.staging = root / "staging"
        self.pnl_automated = root / "pnl_automated"
        self.pnl_manual = root / "pnl_manual"
        self.nio_data_path = gsim / "cc"
        self.dropbox_path = gsim / "dropbox"
        self.run_script = gsim / "run.py"
        self.simsummary_script = gsim / "simsummary.py"
        self.bcorr_script = gsim / "bcorr"
        self.pnl_prod_path = gsim / "pnl_prod"
        self.state_backend = "json"
        self.hostname = "testhost"
        self.host_declared = True


def make_ctx(tmp: Path, *, full: bool = True) -> Ctx:
    """构造应然布局。full=False 留下缺失面(池目录 / dump 软链)。"""
    cfg = FakeConfig(tmp)
    root = cfg.alpha_src.parent
    sidecar = root.with_name(root.name + ".local")

    root.mkdir(parents=True)
    for d in (cfg.alpha_src, cfg.alpha_pnl, cfg.alpha_feature):
        d.mkdir()
    (sidecar / "staging").mkdir(parents=True)
    cfg.staging.symlink_to(Path("..") / sidecar.name / "staging")

    if full:
        for d in (cfg.pnl_automated, cfg.pnl_manual):
            d.mkdir()
        (sidecar / "alpha_dump").mkdir()
        cfg.alpha_dump.symlink_to(Path("..") / sidecar.name / "alpha_dump")

    # 环境面全部就位(gsim / 数据 / dropbox)
    for p in (cfg.nio_data_path, cfg.dropbox_path, cfg.pnl_prod_path):
        p.mkdir(parents=True)
    for f in (cfg.run_script, cfg.simsummary_script, cfg.bcorr_script):
        f.touch()

    legacy = tmp / "mnt-storage" / "alphalib"
    legacy.parent.mkdir(parents=True)
    legacy.symlink_to(root)

    mounts = f"JuiceFS:alphalib {root} fuse.juicefs rw,relatime 0 0\n"
    return Ctx(config=cfg, mounts=mounts, legacy_link=legacy)  # type: ignore[arg-type]


def by_id(results):
    return {r.check_id: r for r in results}


def test_green_layout_no_failures(tmp_path):
    ctx = make_ctx(tmp_path)
    results = run_setup(ctx.config, apply=False, ctx=ctx)
    r = by_id(results)

    assert not has_failures(results)
    for cid in ("mount", "shared-dirs", "pool-dirs", "staging", "dump",
                "legacy-link", "host-declared", "nio-data", "dropbox", "gsim"):
        assert r[cid].status == "ok", f"{cid}: {r[cid].detail}"
    assert r["pg"].status == "skip" and r["lock"].status == "skip"  # json 后端


def test_apply_creates_missing_and_is_idempotent(tmp_path):
    ctx = make_ctx(tmp_path, full=False)
    cfg = ctx.config

    results = run_setup(cfg, apply=True, ctx=ctx)
    r = by_id(results)
    assert r["pool-dirs"].status == "ok" and r["pool-dirs"].fixed
    assert r["dump"].status == "ok" and r["dump"].fixed
    assert cfg.pnl_automated.is_dir() and cfg.pnl_manual.is_dir()
    assert cfg.alpha_dump.is_symlink()
    assert cfg.alpha_dump.resolve() == (
        cfg.alpha_src.parent.with_name("alphalib.local") / "alpha_dump").resolve()

    # 幂等:二跑无需补建
    again = by_id(run_setup(cfg, apply=True, ctx=ctx))
    assert again["pool-dirs"].status == "ok" and not again["pool-dirs"].fixed
    assert again["dump"].status == "ok" and not again["dump"].fixed


def test_check_mode_is_readonly(tmp_path):
    ctx = make_ctx(tmp_path, full=False)
    cfg = ctx.config

    results = run_setup(cfg, apply=False, ctx=ctx)
    r = by_id(results)
    assert r["pool-dirs"].status == "fail" and not r["pool-dirs"].fixed
    assert r["dump"].status == "fail"
    assert not cfg.pnl_automated.exists()          # 零写
    assert not cfg.alpha_dump.exists()
    assert has_failures(results)


def test_wrong_symlink_reported_not_touched(tmp_path):
    ctx = make_ctx(tmp_path)
    cfg = ctx.config
    wrong = tmp_path / "elsewhere"
    wrong.mkdir()
    cfg.alpha_dump.unlink()                        # 撤掉正确软链
    cfg.alpha_dump.symlink_to(wrong)               # 换成指错的

    results = run_setup(cfg, apply=True, ctx=ctx)
    r = by_id(results)
    assert r["dump"].status == "fail" and not r["dump"].fixed
    assert cfg.alpha_dump.resolve() == wrong.resolve()   # apply 没动它


def test_host_declared_states(tmp_path):
    ctx = make_ctx(tmp_path)
    cfg = ctx.config

    cfg.host_declared = False
    r = by_id(run_setup(cfg, apply=False, ctx=ctx))
    assert r["host-declared"].status == "warn"

    cfg.host_declared = None                       # config 无 hosts 块
    r = by_id(run_setup(cfg, apply=False, ctx=ctx))
    assert r["host-declared"].status == "skip"


# ---------------------------------------------------------------------------
# Config hosts 合并层(优先级 env > hosts > vars)
# ---------------------------------------------------------------------------

def _raw():
    return {
        "vars": {"alphalib_root": "/base/alphalib"},
        "hosts": {"server-170": {"alphalib_root": "/ext4/alphalib"}},
        "path": {"alpha_src": "${alphalib_root}/alpha_src"},
    }


def test_resolve_vars_hosts_precedence(monkeypatch):
    monkeypatch.delenv("OPS_ALPHALIB_ROOT", raising=False)

    raw, matched = Config._resolve_vars(_raw(), "server-170")
    assert matched is True
    assert raw["path"]["alpha_src"] == "/ext4/alphalib/alpha_src"

    raw, matched = Config._resolve_vars(_raw(), "unknown-host")
    assert matched is False
    assert raw["path"]["alpha_src"] == "/base/alphalib/alpha_src"

    no_hosts = _raw()
    no_hosts.pop("hosts")
    raw, matched = Config._resolve_vars(no_hosts, "server-170")
    assert matched is None
    assert raw["path"]["alpha_src"] == "/base/alphalib/alpha_src"


def test_resolve_vars_env_beats_hosts(monkeypatch):
    monkeypatch.setenv("OPS_ALPHALIB_ROOT", "/env/alphalib")
    raw, matched = Config._resolve_vars(_raw(), "server-170")
    assert matched is True                          # 命中照记
    assert raw["path"]["alpha_src"] == "/env/alphalib/alpha_src"   # 但 env 赢
