"""E2E fixtures: 真实 gsim + 真实 cc 数据 + 隔离 PG/文件根。

与上层 conftest 的区别:test_config 把 nio_data_path/脚本也 stub 成 tmp(不跑 gsim);
E2E 相反 —— 保留真实 gsim run_cp.py / simsummary / bcorr + 真实 /datasvc/data/cc_2025,
只隔离可写落点(alpha_src/dump/pnl/staging/dropbox → tmp)。

PG 隔离直接复用上层 conftest 的 per-session schema fixture(pg_schema /
pg_conninfo / library_id —— pytest fixture 按目录级联,e2e 组天然可见;
I2,2026-07-11):三表建在随机 schema 内、测试间 wipe、session 结束 DROP
CASCADE,ops_test 不再残留 e2e 测试行。

gsim / cc 数据 / PG 任一不可达时,skip 整个 E2E 组。
"""
import os
from pathlib import Path

import pytest
import yaml

from ops.infra.config import Config, get_project_root

_GSIM_RUN = Path("/usr/local/gsim/run_cp.py")
_CC_DATA = Path("/datasvc/data/cc_2025")


def _read_pg_password() -> str | None:
    env_file = get_project_root() / "scripts" / "postgres" / ".env"
    try:
        for line in env_file.read_text().splitlines():
            if line.strip().startswith("OPS_PG_PASSWORD="):
                return line.strip().split("=", 1)[1]
    except OSError:
        pass
    return None


@pytest.fixture(scope="session")
def gsim_available() -> bool:
    """真实 gsim + cc 数据都在才跑 E2E,否则 skip 整组(PG 可达性与三表引导
    归上层 pg_schema/pg_conninfo fixture)。"""
    if not _GSIM_RUN.exists():
        pytest.skip(f"gsim 不可用 ({_GSIM_RUN}),跳过 E2E")
    if not _CC_DATA.exists():
        pytest.skip(f"cc 数据不可用 ({_CC_DATA}),跳过 E2E")
    return True


@pytest.fixture
def e2e_env(tmp_path, gsim_available, pg_schema, pg_conninfo, library_id, monkeypatch):
    """造一份指向真实 gsim/cc + 隔离落点的 config,返回 (config_path, Config, library_id)。

    隔离:PG 走本 session 的随机 schema(conninfo options=search_path +
    lock_namespace,同单测 test_config);测试前后 wipe 由 library_id fixture
    承担。文件随 tmp_path 自动清。
    check 报告目录也被重定向到 tmp,避免污染 repo 的 docs/reports/check/。
    """

    # check 报告默认写 repo docs/reports/check/ (硬编码,非 config)。E2E 重定向到 tmp,
    # 否则每次跑都往版本库里落一堆 check-Alpha*.json。
    import ops.services.check.report as _report_mod
    _reports = tmp_path / "reports"
    _reports.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_report_mod, "_report_dir", lambda: _reports)

    lib = library_id
    base = yaml.safe_load((get_project_root() / "config.yaml").read_text())
    root = tmp_path / "lib"

    p = base["path"]
    # 隔离可写落点
    for k, sub in [("dropbox_path", "dropbox"), ("alpha_src", "alpha_src"),
                   ("alpha_dump", "alpha_dump"), ("alpha_pnl", "alpha_pnl"),
                   ("alpha_feature", "alpha_feature"), ("staging", "staging"),
                   ("recycle", "recycle"), ("pnl_automated", "pnl_automated"),
                   ("pnl_manual", "pnl_manual"), ("pnl_prod_path", "pnl_prod"),
                   ("pnl_pool_path", "pnl_pool")]:
        p[k] = str(root / sub)
    p["pnl_alphalib"] = str(root / "alpha_pnl")
    p["pnl_path"] = str(root / "ws_pnl")
    p["alpha_path"] = str(root / "ws_alpha")
    p["checkpoint_path"] = str(root / "ws_ckpt")
    # nio_data_path + 脚本保持真实 (E2E 的核心)
    p["nio_data_path"] = str(_CC_DATA)

    base.setdefault("sync", {})["library_id"] = lib
    base["sync"].pop("remote", None)

    # produce 块隔离:config.yaml 带 170 生产路径(/nvme125 dataset 三根),
    # e2e 绝不触碰 —— 三根指 tmp;数据根用真 cc_2025(e2e 的核心是真 gsim);
    # datasvc_prefix 空串 = 不做前缀迁移(160/170 皆可跑);窗口钉死在 cc_2025
    # 可见范围内,产线 e2e 才是确定性的(enddate=TODAY 依赖日历,不可测)。
    base["produce"] = {
        "nio_data_path": str(_CC_DATA),
        "enddate": "20251224",
        "startdate": "20251201",
        "backdays": 256,
        "checkpoint_root": str(root / "produce_checkpoint"),
        "dump_root": str(root / "produce_dump"),
        "pnl_root": str(root / "produce_pnl"),
        "datasvc_prefix": "",
        "module_prefix": str(root / "alpha_src"),
    }

    pw = os.environ.get("OPS_TEST_PG_PASSWORD") or _read_pg_password()
    pg = {"host": os.environ.get("OPS_TEST_PG_HOST", "10.9.100.160"),
          "port": int(os.environ.get("OPS_TEST_PG_PORT", "15432")),
          "dbname": "ops_test",
          "user": os.environ.get("OPS_TEST_PG_USER", "ops"),
          "options": f"-csearch_path={pg_schema}"}
    if pw:
        pg["password"] = pw
    base["state"] = {"backend": "postgres", "postgres": dict(pg),
                     "lock_namespace": pg_schema}

    cfg_path = tmp_path / "config.e2e.yaml"
    cfg_path.write_text(yaml.safe_dump(base, allow_unicode=True))
    config = Config.load(cfg_path)

    for d in (config.dropbox_path, config.alpha_src, config.alpha_dump, config.alpha_pnl,
              config.alpha_feature, config.staging, config.pnl_automated, config.pnl_manual,
              config.pnl_path, config.alpha_path, config.checkpoint_path,
              Path(str(root / "pnl_prod")), Path(str(root / "pnl_pool"))):
        d.mkdir(parents=True, exist_ok=True)

    # 测试前后 wipe 由 library_id fixture 承担(上层 conftest,作用于本
    # session schema);原内联 _wipe(全库 DELETE)随 per-schema 隔离退役。
    yield cfg_path, config, lib


@pytest.fixture
def relax_thresholds(e2e_env):
    """把 e2e config 的 correlation 业绩门槛放宽,让真实 good 因子能走到 ACTIVE。

    pass 路径专用 —— 要验的是 pipeline 路由到 ACTIVE,不是生产门槛(门槛校验由
    correlation_checker 单测覆盖)。correlation-fail 路径不用它,保留严门槛让噪声因子真失败。
    返回重载后的 Config。
    """
    import yaml as _yaml

    from ops.infra.config import Config as _Config

    cfg_path, _, _ = e2e_env
    raw = _yaml.safe_load(cfg_path.read_text())
    raw["checker"]["correlation"].update({
        "ret%": 1.0, "shrp": 0.1, "tvr_d0%": 500.0, "tvr_d1%": 500.0,
        # 1.01 > 任何可能的 |bcorr|(最大 1.0)→ 恒走"低相关直接通过"分支,
        # 使 pass 路径不依赖竞品业绩对比(那是另一条分支,单测已覆盖 _check_beat)。
        "corr_threshold": 1.01,
    })
    cfg_path.write_text(_yaml.safe_dump(raw, allow_unicode=True))
    return _Config.load(cfg_path)


# ---------------------------------------------------------------------------
# 假因子模板:每种在指定 stage 确定性暴雷
# ---------------------------------------------------------------------------

# 正常因子:5min reversal,能跑完整回测(基于真实 AlphaWbaiReversal)
_PY_GOOD = '''\
from gsim import DataRegistry as dr
from gsim import AlphaBase
import numpy as np


class {name}(AlphaBase):
    def __init__(self, cfg):
        AlphaBase.__init__(self, cfg)
        self.vol = dr.getData('volume').data
        self.close = dr.getData('Interval5m.close').data

    def generate(self, di):
        valid_idx = self.valid[di] & (self.vol[di - 1] > 0)
        bar_1 = self.close[di - self.delay, 1, valid_idx]
        bar_42 = self.close[di - self.delay, 42, valid_idx]
        self.alpha[valid_idx] = bar_1 / bar_42
'''

# validate 失败:generate 直接抛异常 → gsim 回测非零退出
_PY_VALIDATE_FAIL = '''\
from gsim import DataRegistry as dr
from gsim import AlphaBase


class {name}(AlphaBase):
    def __init__(self, cfg):
        AlphaBase.__init__(self, cfg)
        self.close = dr.getData('Interval5m.close').data

    def generate(self, di):
        raise RuntimeError("intentional validate-stage blowup")
'''

# checkbias 失败:delay>=1 却访问当日数据 close[di](前视)→ firewall 拦截
_PY_CHECKBIAS_FAIL = '''\
from gsim import DataRegistry as dr
from gsim import AlphaBase


class {name}(AlphaBase):
    def __init__(self, cfg):
        AlphaBase.__init__(self, cfg)
        self.close = dr.getData('Interval5m.close').data

    def generate(self, di):
        valid_idx = self.valid[di]
        # delay>=1 时访问 di 当日 = 前视,firewall 应拦截
        self.alpha[valid_idx] = self.close[di, 1, valid_idx]
'''

# checkpoint 失败:输出依赖非确定随机数 → 断点重跑 v2 md5 不一致
_PY_CHECKPOINT_FAIL = '''\
from gsim import DataRegistry as dr
from gsim import AlphaBase
import numpy as np


class {name}(AlphaBase):
    def __init__(self, cfg):
        AlphaBase.__init__(self, cfg)
        self.close = dr.getData('Interval5m.close').data

    def generate(self, di):
        valid_idx = self.valid[di]
        n = int(valid_idx.sum())
        self.alpha[valid_idx] = np.random.RandomState().randn(n)
'''

# compliance 失败:只选极少数股票(< min_total_stocks=100)
_PY_COMPLIANCE_FAIL = '''\
from gsim import DataRegistry as dr
from gsim import AlphaBase
import numpy as np


class {name}(AlphaBase):
    def __init__(self, cfg):
        AlphaBase.__init__(self, cfg)
        self.close = dr.getData('Interval5m.close').data

    def generate(self, di):
        valid_idx = np.where(self.valid[di])[0]
        # 只给前 10 只股票赋值 → 持股数远低于门槛
        pick = valid_idx[:10]
        self.alpha[pick] = 1.0
'''

# correlation 失败:输出纯噪声 → ret/shrp 远不达标
_PY_CORRELATION_FAIL = '''\
from gsim import DataRegistry as dr
from gsim import AlphaBase
import numpy as np


class {name}(AlphaBase):
    def __init__(self, cfg):
        AlphaBase.__init__(self, cfg)
        self.close = dr.getData('Interval5m.close').data

    def generate(self, di):
        valid_idx = self.valid[di]
        n = int(valid_idx.sum())
        # 用 di 做种子:确定(过 checkpoint)但无预测力(ret/shrp 不达标)
        rng = np.random.RandomState(di)
        self.alpha[valid_idx] = rng.randn(n)
'''


# 完整 XML 模板(基于真实 AlphaWbaiReversal,保证过 gsim schema)
def _xml(name: str, delay: int, discovery_method: str = "manual") -> str:
    return f'''<gsim>
\t<Constants backdays="256" niodatapath="/datasvc/data/cc_2025" niomapprivate="true" authorWeight="wbai:1.0," time_intensive="false" checkpointDays="5" checkpointDir="/tmp/{name}_ckpt/"></Constants>
\t<Universe startdate="20150101" enddate="20251231" secID="/datasvc/rawdata/secID" holidaysfile="/datasvc/rawdata/holidays" calendarfile="/datasvc/rawdata/wind_calendar.csv"></Universe>
\t<Modules>
\t\t<Data id="ALL" module="UmgrAll" path=""></Data>
\t\t<Data id="ALL_TRD" module="UmgrTrd" path=""></Data>
\t\t<Data id="Basedata" module="DmgrBasedata" rawpricePath="" industryPath="" ST="" path="" niomapprivate="true"></Data>
\t\t<Data id="PriceLimit" module="DmgrPriceLimit" dataPath="" path=""></Data>
\t\t<Data id="adjfactor" module="DmgrAdjfactor" dataPath="" niomapprivate="true" path=""></Data>
\t\t<Data id="adjprice" module="DmgrAdjprice" niomapprivate="true" path=""></Data>
\t\t<Data id="ipo" module="DmgrIPO" dataPath="" path=""></Data>
\t\t<Data id="ashareeodprices" module="Dmgrashareeodprices" dataPath="" niomapprivate="true"></Data>
\t\t<Data id="aindexeodprices" module="Dmgraindexeodprices" dataPath="" niomapprivate="true"></Data>
\t\t<Data id="Interval5m" module="DmgrInterval5m" dataPath="" path=""></Data>
\t\t<Alpha id="{name}Mod" module="PLACEHOLDER"></Alpha>
\t</Modules>
\t<Portfolio id="MyPort" booksize="20e6" homecurrency="CNY">
\t\t<Stats module="StatsSimpleV6" mode="0" tradePrice="close" tax="0." fee="0." slippage="0." printStats="true" dumpPnl="true" pnlDir="/tmp/{name}_pnl"></Stats>
\t\t<Alpha id="{name}" module="{name}Mod" universeId="ALL_TRD" booksize="20e6" delay="{delay}" ndays="20" dumpAlphaFile="true" dumpAlphaDir="/tmp/{name}_alpha" st="20">
\t\t\t<Description name="{name}" author="wbai" birthday="20200101" category="test" universe="ALL_TRD" delay="{delay}" discovery_method="{discovery_method}"></Description>
\t\t\t<Operations>
\t\t\t\t<Operation module="AlphaOpDecay" days="3"></Operation>
\t\t\t\t<Operation module="AlphaOpRank" exp="1.0"></Operation>
\t\t\t\t<Operation module="AlphaOpIndNeut" group="sector"></Operation>
\t\t\t</Operations>
\t\t</Alpha>
\t</Portfolio>
</gsim>
'''


_TEMPLATES = {
    "good": (_PY_GOOD, 0),
    "validate": (_PY_VALIDATE_FAIL, 0),
    "checkbias": (_PY_CHECKBIAS_FAIL, 1),   # delay=1 触发前视
    "checkpoint": (_PY_CHECKPOINT_FAIL, 0),
    "compliance": (_PY_COMPLIANCE_FAIL, 0),
    "correlation": (_PY_CORRELATION_FAIL, 0),
}


@pytest.fixture
def make_e2e_factor(e2e_env):
    """在隔离 dropbox 造一个假因子。kind ∈ _TEMPLATES。返回 name。"""
    _, config, _ = e2e_env

    def _make(kind: str, name: str, user: str = "wbai", date: str = "20260705") -> str:
        py_tpl, delay = _TEMPLATES[kind]
        d = config.dropbox_path / user / date / name
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.py").write_text(py_tpl.format(name=name))
        (d / f"Config.{name}.xml").write_text(_xml(name, delay))
        return name

    return _make
