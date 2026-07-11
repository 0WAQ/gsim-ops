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

    raw, matched, env_ovr = Config._resolve_vars(_raw(), "server-170")
    assert matched is True and env_ovr == []
    assert raw["path"]["alpha_src"] == "/ext4/alphalib/alpha_src"

    raw, matched, _ = Config._resolve_vars(_raw(), "unknown-host")
    assert matched is False
    assert raw["path"]["alpha_src"] == "/base/alphalib/alpha_src"

    no_hosts = _raw()
    no_hosts.pop("hosts")
    raw, matched, _ = Config._resolve_vars(no_hosts, "server-170")
    assert matched is None
    assert raw["path"]["alpha_src"] == "/base/alphalib/alpha_src"


def test_resolve_vars_env_beats_hosts_and_is_reported(monkeypatch):
    """env 优先是刻意逃生口,但必须可见(170 残留旧 OPS_ALPHALIB_ROOT
    静默压掉 hosts 声明、迁移目标解析错 —— 2026-07-11 实证)。"""
    monkeypatch.setenv("OPS_ALPHALIB_ROOT", "/env/alphalib")
    raw, matched, env_ovr = Config._resolve_vars(_raw(), "server-170")
    assert matched is True                          # 命中照记
    assert raw["path"]["alpha_src"] == "/env/alphalib/alpha_src"   # 但 env 赢
    assert env_ovr == ["OPS_ALPHALIB_ROOT"]         # 且被显性上报

    # env 值与声明相同 → 不算覆盖,不告警
    monkeypatch.setenv("OPS_ALPHALIB_ROOT", "/ext4/alphalib")
    _, _, env_ovr = Config._resolve_vars(_raw(), "server-170")
    assert env_ovr == []


# ---------------------------------------------------------------------------
# jfs.py:挂载点迁移(migrate-mount,2026-07-11;控制流全注入,无需 root)
# ---------------------------------------------------------------------------

import subprocess

from ops.services.setup.jfs import (
    MigrateError,
    MigrateIO,
    actual_jfs_mount,
    migrate_mount,
    parse_env,
    render_env,
    render_unit,
    writeback_drained,
)

# 170 现役 unit 原文(DISCOVER-170-ENV-RESULT GROUP 2)—— 模板的 golden 锚。
_GOLDEN_UNIT = """[Unit]
Description=JuiceFS mount /ext4/alphalib
After=network-online.target
Wants=network-online.target


[Service]
Type=forking
EnvironmentFile=/etc/juicefs/alphalib.env
ExecStartPre=/bin/mkdir -p /ext4/alphalib
ExecStart=/usr/local/bin/juicefs mount \\
  --cache-dir=/ext4/jfs-cache \\
  --cache-size=102400 \\
  --writeback \\
  --background \\
  redis://mymaster,10.9.100.160,10.9.100.150,10.6.100.144:26380/0 /ext4/alphalib
# 三级 fallback: 标准 umount → fusermount lazy → umount -l
# 防有进程持有 mount 时卡 deactivating。前两步失败也继续 (- 前缀 + bash || 链)。
ExecStop=/bin/bash -c '/usr/local/bin/juicefs umount /ext4/alphalib 2>/dev/null || /bin/fusermount -uz /ext4/alphalib 2>/dev/null || /bin/umount -l /ext4/alphalib 2>/dev/null || true'
Restart=on-failure
RestartSec=5
TimeoutStartSec=60
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
"""

_ENV_TEXT = """# Per-host JuiceFS path override. Written by join.sh.
JFS_MOUNT=/ext4/alphalib
JFS_CACHE_DIR=/ext4/jfs-cache
JFS_LOCAL_DIR=/ext4/alphalib.local
JFS_META_URL=redis://mymaster,10.9.100.160,10.9.100.150,10.6.100.144:26380/0
JFS_REDIS_LOCAL=0
JFS_CACHE_SIZE_MB=102400
"""


def test_render_unit_matches_production_golden():
    """模板正主迁 ops 的正确性锚:老参数渲染 == 170 现役 unit 原文。"""
    got = render_unit("alphalib", Path("/ext4/alphalib"), Path("/ext4/jfs-cache"),
                      "102400",
                      "redis://mymaster,10.9.100.160,10.9.100.150,10.6.100.144:26380/0")
    assert got == _GOLDEN_UNIT


def test_env_parse_render_roundtrip():
    env = parse_env(_ENV_TEXT)
    assert env["JFS_MOUNT"] == "/ext4/alphalib" and len(env) == 6
    out = render_env({**env, "JFS_MOUNT": "/nvme125/alphalib", "NEW_KEY": "x"}, _ENV_TEXT)
    assert "JFS_MOUNT=/nvme125/alphalib" in out
    assert out.startswith("# Per-host")                    # 注释保留
    assert "JFS_META_URL=redis://mymaster" in out          # 其余键原样
    assert out.rstrip().endswith("NEW_KEY=x")              # 新键追加


def test_probe_helpers():
    mounts = "JuiceFS:alphalib /ext4/alphalib fuse.juicefs rw 0 0\nnvme125 /nvme125 zfs rw 0 0\n"
    assert actual_jfs_mount(mounts) == (Path("/ext4/alphalib"), "alphalib")
    assert actual_jfs_mount("nvme125 /nvme125 zfs rw 0 0\n") is None
    assert writeback_drained("juicefs_staging_blocks 0\n")
    assert not writeback_drained("juicefs_staging_blocks 7\n")
    assert not writeback_drained("")                       # 读不到指标按未排干(保守)


class _FakeSys:
    """systemctl 替身 + 挂载状态机:start 成功后目标点'出现'在 mounts 里。"""

    def __init__(self, tmp: Path, target: Path, fail_first_start=False):
        self.tmp, self.target = tmp, target
        self.calls: list[list[str]] = []
        self.mounted = tmp / "ext4" / "alphalib"           # 初始挂载点
        self.fail_first_start = fail_first_start
        self._start_seen = 0

    def run(self, cmd, **kw):
        self.calls.append(list(cmd))
        if cmd[:2] == ["systemctl", "stop"]:
            self.mounted = None
        if cmd[:2] == ["systemctl", "start"]:
            self._start_seen += 1
            if self.fail_first_start and self._start_seen == 1:
                return subprocess.CompletedProcess(cmd, 1, "", "boom")
            # 读回 env 决定挂到哪(回滚时 env 已恢复旧值)
            env = parse_env((self.tmp / "juicefs-poc.env").read_text())
            self.mounted = Path(env["JFS_MOUNT"])
            src = self.mounted / "alpha_src"
            src.mkdir(parents=True, exist_ok=True)
            (src / "AlphaX").mkdir(exist_ok=True)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    def read_mounts(self):
        if self.mounted is None:
            return ""
        return f"JuiceFS:alphalib {self.mounted} fuse.juicefs rw 0 0\n"


def _migrate_fixture(tmp_path, fail_first_start=False):
    old_root = tmp_path / "ext4" / "alphalib"
    (old_root).mkdir(parents=True)
    (old_root / ".stats").write_text("juicefs_staging_blocks 0\n")
    old_local = tmp_path / "ext4" / "alphalib.local"
    (old_local / "staging").mkdir(parents=True)
    (old_local / "alpha_dump").mkdir()

    env_path = tmp_path / "juicefs-poc.env"
    env_path.write_text(_ENV_TEXT
                        .replace("/ext4", str(tmp_path / "ext4")))
    unit_dir = tmp_path / "units"
    unit_dir.mkdir()
    (unit_dir / "juicefs-alphalib.service").write_text(_GOLDEN_UNIT)

    target_disk = tmp_path / "nvme125"
    target_disk.mkdir()
    cfg = FakeConfig(tmp_path)
    cfg.alpha_src = target_disk / "alphalib" / "alpha_src"   # 声明 = nvme125/alphalib

    fake = _FakeSys(tmp_path, target_disk / "alphalib", fail_first_start)
    io = MigrateIO(run=fake.run, env_path=env_path, unit_dir=unit_dir,
                   legacy_link=tmp_path / "mnt" / "alphalib",
                   read_mounts=fake.read_mounts, sleep=lambda s: None)
    return cfg, io, fake, env_path, unit_dir, old_local


def test_migrate_happy_path(tmp_path):
    cfg, io, fake, env_path, unit_dir, old_local = _migrate_fixture(tmp_path)
    log = migrate_mount(cfg, io)

    assert [c[:2] for c in fake.calls] == [
        ["systemctl", "stop"], ["systemctl", "daemon-reload"], ["systemctl", "start"]]
    env = parse_env(env_path.read_text())
    target = str(tmp_path / "nvme125" / "alphalib")
    assert env["JFS_MOUNT"] == target
    assert env["JFS_CACHE_DIR"] == str(tmp_path / "nvme125" / "jfs-cache")  # 同盘沿旧名
    assert env["JFS_META_URL"].startswith("redis://mymaster")               # 未动
    unit = (unit_dir / "juicefs-alphalib.service").read_text()
    assert f"ExecStartPre=/bin/mkdir -p {target}" in unit
    assert (Path(target + ".local") / "staging").is_dir()                   # sidecar 搬到位
    assert not (old_local / "staging").exists()
    assert io.legacy_link.is_symlink() and str(io.legacy_link.resolve()) == target
    assert env_path.with_name(env_path.name + ".ops-migrate-bak").exists()  # 备份在
    assert any("报告不删" not in x and "旧址保留" in x for x in log)


def test_migrate_refuses_when_writeback_dirty(tmp_path):
    cfg, io, fake, env_path, *_ = _migrate_fixture(tmp_path)
    (tmp_path / "ext4" / "alphalib" / ".stats").write_text("juicefs_staging_blocks 3\n")
    before = env_path.read_text()
    with pytest.raises(MigrateError, match="writeback"):
        migrate_mount(cfg, io)
    assert fake.calls == [] and env_path.read_text() == before   # 零改动


def test_migrate_refuses_when_target_disk_missing(tmp_path):
    cfg, io, fake, *_ = _migrate_fixture(tmp_path)
    import shutil as _sh
    _sh.rmtree(tmp_path / "nvme125")
    with pytest.raises(MigrateError, match="不存在"):
        migrate_mount(cfg, io)
    assert fake.calls == []


def test_migrate_start_failure_rolls_back(tmp_path):
    cfg, io, fake, env_path, unit_dir, _ = _migrate_fixture(tmp_path, fail_first_start=True)
    with pytest.raises(MigrateError, match="回滚"):
        migrate_mount(cfg, io)
    env = parse_env(env_path.read_text())
    assert env["JFS_MOUNT"] == str(tmp_path / "ext4" / "alphalib")   # env 已恢复
    assert (unit_dir / "juicefs-alphalib.service").read_text() == _GOLDEN_UNIT
    assert [c[:2] for c in fake.calls][-1] == ["systemctl", "start"]  # 回滚重启了旧配置


def test_migrate_noop_when_already_at_target(tmp_path):
    cfg, io, fake, *_ = _migrate_fixture(tmp_path)
    cfg.alpha_src = tmp_path / "ext4" / "alphalib" / "alpha_src"      # 声明 == 实挂
    log = migrate_mount(cfg, io)
    assert any("无需迁移" in x for x in log) and fake.calls == []


def test_mount_check_hints_migrate(tmp_path):
    """声明未挂 + JFS 在别处 → mount 项 detail 给 migrate 指引。"""
    ctx = make_ctx(tmp_path)
    ctx.mounts = f"JuiceFS:alphalib {tmp_path}/elsewhere fuse.juicefs rw 0 0\n"
    r = by_id(run_setup(ctx.config, apply=False, ctx=ctx))
    assert r["mount"].status == "fail"
    assert "--migrate-mount" in r["mount"].detail
