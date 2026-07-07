"""Shared pytest fixtures for the ops test suite.

隔离原则:
- PG 走独立库 `ops_test` (同 160 docker 实例),每个测试用独立 library_id 分区,
  测后按 library_id 删三表行 —— 绝不碰生产 `ops` 库。
- 文件走 tmp_path,五条数据路径 + staging/recycle/checkpoint/pnl 全部相对它。

pg fixture 在库不可达时 pytest.skip 整组,CI / 无 PG 环境不红。
"""
import os
import uuid
from pathlib import Path

import pytest
import yaml

from ops.infra.config import get_project_root


# ---------------------------------------------------------------------------
# Postgres 测试库连接
# ---------------------------------------------------------------------------

def _read_pg_password() -> str | None:
    """从 scripts/postgres/.env 读 OPS_PG_PASSWORD (与生产同一密码文件)。"""
    env_file = get_project_root() / "scripts" / "postgres" / ".env"
    try:
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line.startswith("OPS_PG_PASSWORD="):
                return line.split("=", 1)[1]
    except OSError:
        pass
    return None


def _test_conninfo(dbname: str = "ops_test") -> str:
    """测试库 conninfo。可用 OPS_TEST_PG_* 环境变量覆盖。"""
    host = os.environ.get("OPS_TEST_PG_HOST", "10.9.100.160")
    port = os.environ.get("OPS_TEST_PG_PORT", "15432")
    user = os.environ.get("OPS_TEST_PG_USER", "ops")
    pwd = os.environ.get("OPS_TEST_PG_PASSWORD") or _read_pg_password()
    parts = [f"host={host}", f"port={port}", f"dbname={dbname}", f"user={user}"]
    if pwd:
        parts.append(f"password={pwd}")
    return " ".join(parts)


@pytest.fixture(scope="session")
def pg_conninfo() -> str:
    """测试库 conninfo;不可达则 skip 整个 pg 组。"""
    import psycopg

    conninfo = _test_conninfo()
    try:
        conn = psycopg.connect(conninfo, connect_timeout=5)
        conn.close()
    except Exception as e:  # noqa: BLE001 — 任何连接失败都 skip
        pytest.skip(f"ops_test PG 不可达,跳过 pg 测试: {e}")
    return conninfo


@pytest.fixture
def library_id(pg_conninfo) -> str:
    """每个测试一个唯一 library_id,测后按 library_id 删三表行 (并行安全)。"""
    import psycopg

    lib = f"test_{uuid.uuid4().hex[:12]}"
    yield lib
    # teardown: 清掉本 library_id 在三张表的所有行
    try:
        conn = psycopg.connect(pg_conninfo, autocommit=True, connect_timeout=5)
        for tbl in ("factor_state",):  # 旧 library_id 分区清理,I2 重建时整体替换
            try:
                conn.execute(f"DELETE FROM {tbl} WHERE library_id = %s", (lib,))
            except psycopg.errors.UndefinedTable:
                pass  # 表还没被任何 store __init__ 建出来
        conn.close()
    except Exception:  # noqa: BLE001 — teardown 尽力而为
        pass


# ---------------------------------------------------------------------------
# Store 直连 (绕过 Config)
# ---------------------------------------------------------------------------

@pytest.fixture
def state_store(pg_conninfo):
    # 2026-07-07 诚实化:三表拆分删掉了 library_id 分区,本 fixture 原签名
    # PostgresStateStore(conninfo, library_id=...) 直接 TypeError —— 即 PG 组
    # 自重构以来从未真正跑过。隔离模型需重建为 per-test schema(CREATE SCHEMA
    # t_<uuid> + search_path)且 put 前需 factor_info 父行(FK),须对着真 PG
    # 迭代,见 full-review 第二部分 I2。在此之前显式 skip,不再假装可用。
    pytest.skip("PG store fixtures 待重建 (per-schema 隔离 + FK 种子行, full-review I2)")


# derived_store fixture 已随 derived 层删除 (2026-07-07 Wave 2, JOURNAL V2)


# ---------------------------------------------------------------------------
# 隔离 Config (文件 → tmp_path, state → ops_test)
# ---------------------------------------------------------------------------

@pytest.fixture
def test_config(tmp_path: Path, pg_conninfo, library_id):
    """基于真实 config.yaml 造一份隔离 config:
    - 所有数据路径重定位到 tmp_path
    - state backend=postgres 指向 ops_test + 本测试 library_id

    返回 (config_path, Config 实例)。
    """
    from ops.infra.config import Config

    base = yaml.safe_load((get_project_root() / "config.yaml").read_text())

    root = tmp_path / "alphalib"
    # vars 里的 alphalib_root 决定五条数据路径 + staging/recycle
    base.setdefault("vars", {})
    base["vars"]["alphalib_root"] = str(root)
    base["vars"]["workspace"] = str(tmp_path / "workspace")

    # 逐条兜底重写 path(不依赖 vars 里恰好有对应变量)
    p = base["path"]
    p["dropbox_path"] = str(tmp_path / "dropbox")  # 隔离: 绝不碰生产 dropbox
    p["alpha_src"] = str(root / "alpha_src")
    p["alpha_dump"] = str(root / "alpha_dump")
    p["alpha_pnl"] = str(root / "alpha_pnl")
    p["alpha_feature"] = str(root / "alpha_feature")
    p["staging"] = str(root / "staging")
    p["recycle"] = str(root / "recycle")
    p["pnl_automated"] = str(root / "pnl_automated")
    p["pnl_manual"] = str(root / "pnl_manual")
    p["pnl_path"] = str(tmp_path / "workspace" / "pnl")
    p["alpha_path"] = str(tmp_path / "workspace" / "alpha")
    p["checkpoint_path"] = str(tmp_path / "workspace" / "checkpoint")
    p["nio_data_path"] = str(tmp_path / "nio")  # 不存在 → _build_npy_index 返回 {}
    # bcorr 对比池 (correlation 读路径用)。fake checker 不跑真 correlation,故当前测试不碰,
    # 但仍重定位到 tmp,防止将来扩测试误读/误写生产池。
    p["pnl_prod_path"] = str(tmp_path / "pnl_prod")
    p["pnl_alphalib"] = str(root / "alpha_pnl")
    p["pnl_pool_path"] = str(tmp_path / "pnl_pool")

    # library_id 显式固定(否则默认取 alpha_src.parent.name = "alphalib")
    base.setdefault("sync", {})
    base["sync"]["library_id"] = library_id
    base["sync"].pop("remote", None)

    # state 指向 ops_test
    pw = _read_pg_password()
    pg_block = {
        "host": os.environ.get("OPS_TEST_PG_HOST", "10.9.100.160"),
        "port": int(os.environ.get("OPS_TEST_PG_PORT", "15432")),
        "dbname": "ops_test",
        "user": os.environ.get("OPS_TEST_PG_USER", "ops"),
    }
    if pw:
        pg_block["password"] = pw
    base["state"] = {"backend": "postgres", "postgres": dict(pg_block)}

    cfg_path = tmp_path / "config.test.yaml"
    cfg_path.write_text(yaml.safe_dump(base, allow_unicode=True))
    config = Config.load(cfg_path)

    # 预建数据目录(pipeline __init__ 只 mkdir 一部分)
    for d in (config.staging, config.alpha_src, config.alpha_dump, config.alpha_pnl,
              config.alpha_feature, config.pnl_automated, config.pnl_manual,
              config.pnl_path, config.alpha_path, config.checkpoint_path,
              config.dropbox_path):
        d.mkdir(parents=True, exist_ok=True)

    return cfg_path, config


# ---------------------------------------------------------------------------
# 合法 staging 因子生成
# ---------------------------------------------------------------------------

_MINIMAL_PY = '''\
from gsim import DataRegistry as dr
from gsim import AlphaBase


class {name}(AlphaBase):
    def __init__(self, cfg):
        AlphaBase.__init__(self, cfg)
        self.close = dr.getData('ashareeodprices.s_dq_close').data

    def generate(self, di):
        self.alpha[:] = self.close[di - self.delay]
'''


def _minimal_xml(name: str, delay: int, discovery_method: str | None) -> str:
    dm = f' discovery_method="{discovery_method}"' if discovery_method is not None else ""
    return f'''<gsim>
\t<Constants backdays="256" niodatapath="/datasvc/data/cc_2025" niomapprivate="true"></Constants>
\t<Universe startdate="20150101" enddate="20251231"></Universe>
\t<Modules>
\t\t<Data id="ALL_TRD" module="UmgrTrd" path=""></Data>
\t\t<Data id="ashareeodprices" module="Dmgrashareeodprices" dataPath="" niomapprivate="true"></Data>
\t\t<Alpha id="{name}Mod" module="PLACEHOLDER"></Alpha>
\t</Modules>
\t<Portfolio id="MyPort" booksize="20e6" homecurrency="CNY">
\t\t<Stats module="StatsSimpleV6" mode="0" tradePrice="close" dumpPnl="true" pnlDir="/tmp/pnl"></Stats>
\t\t<Alpha id="{name}" module="{name}Mod" universeId="ALL_TRD" booksize="20e6" delay="{delay}" ndays="20" dumpAlphaFile="true" dumpAlphaDir="/tmp/alpha">
\t\t\t<Description name="{name}" author="wbai" birthday="20200101" category="test" universe="ALL_TRD" delay="{delay}"{dm}></Description>
\t\t\t<Operations>
\t\t\t\t<Operation module="AlphaOpRank" exp="1.0"></Operation>
\t\t\t</Operations>
\t\t</Alpha>
\t</Portfolio>
</gsim>
'''


@pytest.fixture
def make_factor(test_config):
    """在 staging 造一个合法因子 (含 meta.json)。

    返回 callable(name, author, delay, discovery_method, submitted_by) → factor_dir。
    默认制造一个 submit 后应有的完整 staging 目录。
    """
    from ops.core.factormeta import FactorMeta

    _, config = test_config

    def _make(name: str = "AlphaWbaiTest",
              author: str = "wbai",
              delay: int = 0,
              discovery_method: str | None = "manual",
              submitted_by: str | None = None) -> Path:
        d = config.staging / name
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.py").write_text(_MINIMAL_PY.format(name=name))
        (d / f"Config.{name}.xml").write_text(
            _minimal_xml(name, delay, discovery_method))
        meta = FactorMeta(
            name=name, author=author, birthday=20200101, universe="ALL_TRD",
            category="test", delay=delay, backdays=256, dump_alpha=True,
            has_intraday_curve=False, discovery_method=discovery_method,
            submitted_at="2026-07-05T00:00:00",
            submitted_by=submitted_by or author,
        )
        meta.save(d / "meta.json")
        return d

    return _make


# ---------------------------------------------------------------------------
# Fake checkers (依赖注入到 CheckerPipeline)
# ---------------------------------------------------------------------------

class _FakeChecker:
    """一个 stage 的假 checker。默认 pass;可配置抛 CheckFail/CheckSkip/Exception。

    记录调用顺序到共享的 call_log,用于验证 short-circuit。
    """
    def __init__(self, stage: str, behavior: str, call_log: list,
                 corr_result=None, raise_on_persist: bool = False):
        self.stage = stage
        self.behavior = behavior  # "pass" | "fail" | "skip" | "crash"
        self.call_log = call_log
        self.corr_result = corr_result

    def check(self, factor):
        from ops.services.check.checker.base import CheckFail, CheckSkip
        self.call_log.append(self.stage)
        if self.behavior == "fail":
            raise CheckFail(self.stage, f"fake fail at {self.stage}")
        if self.behavior == "skip":
            raise CheckSkip(self.stage, f"fake skip at {self.stage}")
        if self.behavior == "crash":
            raise RuntimeError(f"fake crash at {self.stage}")
        # pass: checkpoint checker 还会被调 .clean();correlation 返回 corr_result
        if self.stage == "correlation":
            return self.corr_result
        return None

    def clean(self, factor):
        # 只有 checkpoint checker 有 clean();其余不会被调
        pass


@pytest.fixture
def fake_checkers():
    """生成一组 fake checker (dict[stage → checker]) 用于注入。

    返回 callable(fail_stage=None, behavior="fail", corr_result=None) → (checkers, call_log)。
    fail_stage=None → 全 pass;否则指定 stage 按 behavior 出错,其余 pass。
    """
    from ops.services.check.check import STAGES

    def _make(fail_stage: str | None = None, behavior: str = "fail", corr_result=None):
        call_log: list[str] = []
        checkers = {}
        for st in STAGES:
            b = behavior if st == fail_stage else "pass"
            checkers[st] = _FakeChecker(st, b, call_log, corr_result=corr_result)
        return checkers, call_log

    return _make


@pytest.fixture
def fake_metrics(monkeypatch):
    """monkeypatch Runner.run_simsummary 返回一个假 Metrics (pass 路径 archive 需要)。"""
    from ops.core.metrics import Metrics
    import ops.services.check.check as check_mod

    m = Metrics(ret=15.0, tvr=40.0, shrp=2.5, mdd=8.0, fitness=1.2)
    monkeypatch.setattr(check_mod.Runner, "run_simsummary", staticmethod(lambda *a, **k: m))
    return m


# ---------------------------------------------------------------------------
# dropbox 因子生成 (submit 从 dropbox 读, 不是 staging)
# ---------------------------------------------------------------------------

@pytest.fixture
def make_dropbox_factor(test_config):
    """在 dropbox/<user>/<date>/<name>/ 造一个因子源 (submit 的输入)。

    返回 callable(name, user, date, delay, discovery_method) → factor_dir。
    默认 discovery_method="manual"。传 None 造缺失(测硬校验)。
    """
    _, config = test_config

    def _make(name: str = "AlphaWbaiTest",
              user: str = "wbai",
              date: str = "20260705",
              delay: int = 0,
              discovery_method: str | None = "manual") -> Path:
        d = config.dropbox_path / user / date / name
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.py").write_text(_MINIMAL_PY.format(name=name))
        (d / f"Config.{name}.xml").write_text(
            _minimal_xml(name, delay, discovery_method))
        return d

    return _make


# ---------------------------------------------------------------------------
# args namespace (驱动 run_* 入口)
# ---------------------------------------------------------------------------

@pytest.fixture
def make_args(test_config):
    """构造一个模拟 argparse.Namespace 的对象喂给 run_*(args)。

    自动带上 config_path;其余字段按需 kwargs 覆盖。yes=True 默认跳过交互确认。
    """
    from types import SimpleNamespace

    cfg_path, _ = test_config

    def _make(**kwargs):
        defaults = dict(
            config_path=cfg_path,
            yes=True,
            user=None,
            factor_name=None,
        )
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    return _make

