"""Shared pytest fixtures for the ops test suite.

隔离原则(I2,2026-07-11 重建):
- PG 走独立库 `ops_test`,**每个 pytest session 一个随机 schema**
  (`t_<hex>`,conninfo 带 `options=-csearch_path=…`,三表建在 schema 内,
  session 结束 DROP SCHEMA CASCADE)—— 并行的 pytest 进程各有各的 schema,
  互不干扰;绝不碰生产 `ops` 库。测试之间沿用 wipe(schema 内删 factor_info
  级联)。
- **advisory lock 是库级作用域,schema 隔离挡不住它** —— 测试 config 注入
  `state.lock_namespace = <schema 名>`(lock.py 的仅测试注入口),并行 session
  各锁各的命名空间。
- 文件走 tmp_path,五条数据路径 + staging/recycle/checkpoint/pnl 全部相对它。

pg fixture 在库不可达时 pytest.skip 整组,无 PG 环境不红;本地/CI 起测试 PG
见 tests/README.md(docker-compose.test.yml / ci.yml postgres service)。
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
def pg_schema() -> str:
    """per-session 随机 PG schema(I2 隔离核心):建 `t_<hex>` → yield 名字 →
    session 结束 DROP SCHEMA CASCADE。不可达则 skip 整个 pg 组。

    双保险:建/删 schema 前都确认 current_database() == 'ops_test',
    防 OPS_TEST_PG_* 误指生产库。
    """
    import psycopg

    base = _test_conninfo()
    schema = f"t_{uuid.uuid4().hex[:12]}"
    try:
        conn = psycopg.connect(base, autocommit=True, connect_timeout=5)
    except Exception as e:  # noqa: BLE001 — 任何连接失败都 skip
        pytest.skip(f"ops_test PG 不可达,跳过 pg 测试: {e}")
    try:
        db = conn.execute("SELECT current_database()").fetchone()[0]
        if db != "ops_test":
            pytest.skip(f"测试库不是 ops_test(是 {db}),拒绝建 schema")
        conn.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        conn.close()

    yield schema

    try:
        conn = psycopg.connect(base, autocommit=True, connect_timeout=5)
        if conn.execute("SELECT current_database()").fetchone()[0] == "ops_test":
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        conn.close()
    except Exception:  # noqa: BLE001 — teardown 尽力而为(残留 schema 无害,可手工清)
        pass


@pytest.fixture(scope="session")
def pg_conninfo(pg_schema: str) -> str:
    """测试库 conninfo,search_path 钉在本 session 的 schema(只列 schema 本身,
    不含 public —— 漏建的表直接 UndefinedTable 响亮失败,不会静默落到 public)。

    按 FK 依赖序 (info → state → snapshot) 在 schema 内引导三表:空库上
    factor_state 的内联 FK 引用 factor_info,若 state store 先连,
    CREATE TABLE 直接 UndefinedTable。
    """
    conninfo = f"{_test_conninfo()} options=-csearch_path={pg_schema}"

    # DDL 已滚出 store __init__(2026-07-09 阶段 2):显式按 FK 依赖序引导三表
    from ops.infra.schema import ensure_schemas
    ensure_schemas(conninfo)
    return conninfo


def wipe_test_db(conninfo: str) -> None:
    """清空因子表(删 factor_info,FK 级联 state/snapshot;factor_history
    无 FK 单独清 —— 事件设计上活过删除,测试隔离必须显式截断,v2b)。

    conninfo 带 search_path 时作用于本 session 的 schema(测试间隔离);
    只对 current_database() == 'ops_test' 生效 —— 双保险,防 OPS_TEST_PG_*
    误指生产库时把生产清了。
    """
    import psycopg

    try:
        conn = psycopg.connect(conninfo, autocommit=True, connect_timeout=5)
        db = conn.execute("SELECT current_database()").fetchone()[0]
        if db == "ops_test":
            for sql in ("DELETE FROM factor_info", "DELETE FROM factor_history"):
                try:
                    conn.execute(sql)
                except psycopg.errors.UndefinedTable:
                    pass  # 表还没被任何 store __init__ 建出来
        conn.close()
    except Exception:  # noqa: BLE001 — 清理尽力而为
        pass


@pytest.fixture
def library_id(pg_conninfo) -> str:
    """每个测试一个唯一 library_id(现仅用于 cache 路径命名)+ 测试间表级隔离。

    测试前后各清一次三表 —— conninfo 带本 session 的 search_path,只清自己
    schema 里的行;跨进程隔离由 per-session schema 承担(I2,2026-07-11),
    并行跑 pytest 不再互相踩。
    """
    lib = f"test_{uuid.uuid4().hex[:12]}"
    wipe_test_db(pg_conninfo)
    yield lib
    wipe_test_db(pg_conninfo)


# ---------------------------------------------------------------------------
# Store 直连 (绕过 Config)
# ---------------------------------------------------------------------------

@pytest.fixture
def state_store(pg_conninfo, library_id):
    """PostgresStateStore 直连(I2 重建,2026-07-11)。

    library_id 依赖只为测试前后 wipe;写 factor_state 前必须有 factor_info
    父行(FK)—— 用 seed_info fixture 先种,镜像生产前置
    (register 是 info+state 原子双表写,生产不存在无父行的 state)。
    """
    from ops.infra.store.pg_store import PostgresStateStore

    return PostgresStateStore(pg_conninfo)


@pytest.fixture
def seed_info(pg_conninfo):
    """种 factor_info 父行(direct-store 测试用;FK 要求 state 写入前父行先在)。

    返回 callable(*names)。service 级测试请用 seed_factor(走 Config)。
    """
    from ops.infra.info import FactorInfo
    from ops.infra.info.pg_store import PostgresInfoStore

    info_store = PostgresInfoStore(pg_conninfo)

    def _seed(*names: str):
        for n in names:
            info_store.upsert(FactorInfo(name=n, author="wbai",
                                         discovery_method="manual",
                                         created_at="2026-07-05T00:00:00"))

    return _seed


# derived_store fixture 已随 derived 层删除 (2026-07-07 Wave 2, JOURNAL V2)


# ---------------------------------------------------------------------------
# 隔离 Config (文件 → tmp_path, state → ops_test)
# ---------------------------------------------------------------------------

def _isolated_paths(base: dict, tmp_path: Path, library_id: str) -> None:
    """把 config 的全部数据路径重定位到 tmp_path(test_config / json_config 公用;
    state 后端由调用方决定)。"""
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


def _mkdirs(config) -> None:
    """预建数据目录(pipeline __init__ 只 mkdir 一部分)。"""
    for d in (config.staging, config.alpha_src, config.alpha_dump, config.alpha_pnl,
              config.alpha_feature, config.pnl_automated, config.pnl_manual,
              config.pnl_path, config.alpha_path, config.checkpoint_path,
              config.dropbox_path):
        d.mkdir(parents=True, exist_ok=True)


@pytest.fixture
def test_config(tmp_path: Path, pg_schema, pg_conninfo, library_id):
    """基于真实 config.yaml 造一份隔离 config:
    - 所有数据路径重定位到 tmp_path
    - state backend=postgres 指向 ops_test + 本 session schema(search_path)
    - lock_namespace = schema 名(advisory lock 库级作用域,并行 session 各锁各的)

    返回 (config_path, Config 实例)。
    """
    from ops.infra.config import Config

    base = yaml.safe_load((get_project_root() / "config.yaml").read_text())
    _isolated_paths(base, tmp_path, library_id)

    # state 指向 ops_test 的本 session schema
    pw = os.environ.get("OPS_TEST_PG_PASSWORD") or _read_pg_password()
    pg_block = {
        "host": os.environ.get("OPS_TEST_PG_HOST", "10.9.100.160"),
        "port": int(os.environ.get("OPS_TEST_PG_PORT", "15432")),
        "dbname": "ops_test",
        "user": os.environ.get("OPS_TEST_PG_USER", "ops"),
        "options": f"-csearch_path={pg_schema}",
    }
    if pw:
        pg_block["password"] = pw
    base["state"] = {"backend": "postgres", "postgres": dict(pg_block),
                     "lock_namespace": pg_schema}

    cfg_path = tmp_path / "config.test.yaml"
    cfg_path.write_text(yaml.safe_dump(base, allow_unicode=True))
    config = Config.load(cfg_path)
    _mkdirs(config)
    return cfg_path, config


@pytest.fixture
def json_config(tmp_path: Path, monkeypatch):
    """json 后端隔离 config(无 PG):路径重定位同 test_config,state backend=json,
    json state 文件(CACHE_ROOT)与 fcntl 锁目录(LOCK_DIR)一并隔离到 tmp。

    适用于不碰 info/snapshot store(PG-only)的控制流测试,
    见 tests/test_check_routing_json.py。返回 (config_path, Config 实例)。
    """
    import ops.infra.cache as cache_mod
    import ops.infra.lock as lock_mod
    from ops.infra.config import Config

    base = yaml.safe_load((get_project_root() / "config.yaml").read_text())
    _isolated_paths(base, tmp_path, "jsontest")
    base["state"] = {"backend": "json"}

    # CACHE_ROOT/LOCK_DIR 是 import 时算好的模块常量,改 HOME 环境变量无效
    monkeypatch.setattr(cache_mod, "CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(lock_mod, "LOCK_DIR", tmp_path / "locks")

    cfg_path = tmp_path / "config.json-backend.yaml"
    cfg_path.write_text(yaml.safe_dump(base, allow_unicode=True))
    config = Config.load(cfg_path)
    _mkdirs(config)
    return cfg_path, config


@pytest.fixture
def seed_factor(test_config):
    """种一条 factor_info + factor_state(name, status, author=..., **state 字段)。

    三表拆分后测试不能再裸 `store.put(FactorRecord(...))`:author 在
    factor_info,且 factor_state.name 的外键要求 info 父行先在(直接 put →
    ForeignKeyViolation,生产验证第一轮 11 个失败的根因)。
    """
    from ops.core.state import CheckRecord, FactorRecord, FactorStatus
    from ops.infra.info import FactorInfo, default_info_store
    from ops.infra.store import default_store

    _, config = test_config
    info_store = default_info_store(config)
    store = default_store(config)

    def _seed(name: str, status, author: str = "wbai",
              discovery_method: str | None = "manual", **state_kw):
        # v2b:last_fail_* 不再是 state 列 —— 种一条失败 check 事件等价表达
        # ("最近失败"是 factor_history/check_history 的派生事实)
        fail_stage = state_kw.pop("last_fail_stage", None)
        fail_reason = state_kw.pop("last_fail_reason", "seeded fail")
        info_store.upsert(FactorInfo(name=name, author=author,
                                     discovery_method=discovery_method,
                                     created_at="2026-07-05T00:00:00"))
        state_kw.setdefault("updated_at", "2026-07-05T00:00:00")
        if status == FactorStatus.ACTIVE:
            # 镜像生产不变量(chk_active_entered,schema v2a):ACTIVE 必有
            # 入库时刻 —— 全部生产写路径都遵守,测试种子同样遵守
            state_kw.setdefault("entered_at", "2026-07-01T00:00:00")
        store.put(FactorRecord(name=name, status=status, **state_kw))
        if fail_stage:
            store.append_check(name, CheckRecord(
                started_at="2026-07-05T00:00:00",
                finished_at="2026-07-05T00:05:00",
                passed=False, failed_stage=fail_stage,
                fail_reason=fail_reason))

    return _seed


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


def _minimal_xml(name: str, delay: int, discovery_method: str | None,
                 birthday: int = 20200101) -> str:
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
\t\t\t<Description name="{name}" author="wbai" birthday="{birthday}" category="test" universe="ALL_TRD" delay="{delay}"{dm}></Description>
\t\t\t<Operations>
\t\t\t\t<Operation module="AlphaOpRank" exp="1.0"></Operation>
\t\t\t</Operations>
\t\t</Alpha>
\t</Portfolio>
</gsim>
'''


@pytest.fixture
def write_factor():
    """底层工厂:往**给定 config** 的 staging 写一个合法因子(含 meta.json)。

    不依赖任何 PG fixture —— PG 组(make_factor)与 json 后端的控制流测试
    (test_check_routing_json)共用同一份因子模板,避免 XML/py 模板克隆漂移。
    """
    from ops.core.factormeta import FactorMeta

    def _write(config,
               name: str = "AlphaWbaiTest",
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

    return _write


@pytest.fixture
def make_factor(test_config, write_factor):
    """在 (PG) test_config 的 staging 造一个合法因子。

    返回 callable(name, author, delay, discovery_method, submitted_by) → factor_dir。
    默认制造一个 submit 后应有的完整 staging 目录。
    """
    _, config = test_config

    def _make(name: str = "AlphaWbaiTest",
              author: str = "wbai",
              delay: int = 0,
              discovery_method: str | None = "manual",
              submitted_by: str | None = None) -> Path:
        return write_factor(config, name=name, author=author, delay=delay,
                            discovery_method=discovery_method,
                            submitted_by=submitted_by)

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
            # stage 由流水线捕获时按 current_stage 归因,exception 不携带;
            # corr_result 挂 .result 镜像真 correlation checker(v3 测得快照)
            raise CheckFail(f"fake fail at {self.stage}", result=self.corr_result)
        if self.behavior == "skip":
            raise CheckSkip(f"fake skip at {self.stage}")
        if self.behavior == "crash":
            raise RuntimeError(f"fake crash at {self.stage}")
        # pass: correlation 返回 corr_result 供 archive 落 bcorr 快照
        if self.stage == "correlation":
            return self.corr_result
        return None

    def clean(self, factor):
        # 流水线对每个 stage 统一调 clean()(默认 no-op)
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
    import ops.services.check.check as check_mod
    from ops.core.metrics import Metrics

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
              discovery_method: str | None = "manual",
              birthday: int = 20200101) -> Path:
        d = config.dropbox_path / user / date / name
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.py").write_text(_MINIMAL_PY.format(name=name))
        (d / f"Config.{name}.xml").write_text(
            _minimal_xml(name, delay, discovery_method, birthday=birthday))
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

