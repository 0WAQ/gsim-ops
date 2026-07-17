import os
import re
import socket
from pathlib import Path
from typing import Any

import yaml


def get_project_root() -> Path:
    """Find project root directory (contains pyproject.toml)."""
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


def get_default_config_path() -> Path:
    """
    Get default config path with priority:
    1. Environment variable OPS_CONFIG
    2. ./config.yaml (current directory)
    3. {project_root}/config.yaml

    config.yaml = 生产默认 (JFS 路径 + Postgres state)。没有回退配置 ——
    config.prod-legacy.yaml 已删除 (假保险)。

    OPS_CONFIG 一旦设置就是唯一候选,**不存在也不回落**——静默跳过会让 typo
    的路径默默换成别的 config 去操作因子库;存在性检查统一在 Config.load
    响亮失败。本函数在 parser 注册期被调用(cli/common 的 default=),
    不得在此退出/抛错,否则 `ops --help` 都会炸。
    """
    env_config = os.environ.get("OPS_CONFIG")
    if env_config:
        return Path(env_config)

    # cwd 可能已被删除(Path.cwd() 抛 OSError)——"永不抛"契约必须密闭,
    # 返回一个必然不存在的相对路径,由 Config.load 响亮报错
    try:
        cwd_config = Path.cwd() / "config.yaml"
        if cwd_config.exists():
            return cwd_config
        # uv tool install 场景 get_project_root() 找不到 pyproject.toml 会退化成
        # cwd(见彼处),此路径可能不存在 —— 由 Config.load 给可行动的报错
        return get_project_root() / "config.yaml"
    except OSError:
        return Path("config.yaml")


class Config:
    def __init__(self, config: dict[str, Any]):
        # hosts 声明命中情况(load() 回填;直接构造 Config(raw) 的路径保持缺省。
        # ops setup 的 host-declared 检查项消费)
        self.hostname: str = ""
        self.host_declared: bool | None = None
        self.env_overrides: list[str] = []

        # checker
        self.compliance: dict[str, Any] = config["checker"]["compliance"]
        self.correlation: dict[str, Any] = config["checker"]["correlation"]
        self.checkpoint: dict[str, Any] = config["checker"]["checkpoint"]

        # path
        self.dropbox_path = Path(config["path"]["dropbox_path"])
        self.pnl_prod_path = Path(config["path"]["pnl_prod_path"])
        self.pnl_pool_path = Path(config["path"]["pnl_pool_path"])
        self.pnl_alphalib = Path(config["path"]["pnl_alphalib"])
        self.pnl_automated = Path(config["path"]["pnl_automated"])
        self.pnl_manual = Path(config["path"]["pnl_manual"])
        self.python_path = Path(config["path"]["python_path"])

        self.alpha_src = Path(config["path"]["alpha_src"])
        self.alpha_dump = Path(config["path"]["alpha_dump"])
        self.alpha_pnl = Path(config["path"]["alpha_pnl"])
        self.alpha_feature = Path(config["path"]["alpha_feature"])
        self.staging = Path(config["path"]["staging"])
        self.recycle = Path(config["path"]["recycle"])

        self.pnl_path = Path(config["path"]["pnl_path"])
        self.alpha_path = Path(config["path"]["alpha_path"])
        self.checkpoint_path = Path(config["path"]["checkpoint_path"])
        self.nio_data_path = Path(config["path"]["nio_data_path"])

        # script
        self.run_script = Path(config["script"]["run_script"])
        self.simsummary_script = Path(config["script"]["simsummary_script"])
        self.bcorr_script = Path(config["script"]["bcorr_script"])
        self.feishu_script = Path(config["script"]["feishu_script"])

        # backtest
        self.stats = config["backtest"]["stats"]
        self.thres = "90"

        # authors:  # TODO:
        self.authors: dict[str, dict[str, str]] = config["authors"]
        self.summary_emails: dict[str, list[str]] = config["notification"][
            "summary_emails"
        ]
        self.send_author_email: bool = bool(config["notification"]["send_author_email"])

        # mode
        self.max_workers: int = config["mode"]["max_workers"]
        self.dry_run: bool = config["mode"]["dry_run"]
        self.timeout: int = config["mode"]["timeout"]

        # produce: 因子日增生产(ops produce)。与 check 是两个事实族:
        # path.nio_data_path 是 check 的验证窗口(cc_2025,.meta 冻结可复现),
        # produce.nio_data_path 是生产的日增数据根(cc_all,持续增长)——
        # 两个键不是重复,别"归一"。块缺失不炸构造(dev/test config 无需配),
        # run_produce 入口缺键响亮报错。
        produce_cfg: dict[str, Any] = config.get("produce") or {}
        _p = produce_cfg.get("nio_data_path")
        self.produce_nio_data_path: Path | None = Path(_p) if _p else None
        self.production_start: str | None = (
            str(produce_cfg["production_start"]) if "production_start" in produce_cfg
            else None)
        _w = produce_cfg.get("workspace")
        self.produce_workspace: Path | None = Path(_w) if _w else None
        # 就绪判定 canary 数据目录(所有因子至少依赖行情基准节奏)
        self.produce_readiness_dirs: list[str] = (
            list(produce_cfg.get("readiness_dirs") or ["Basedata"]))

        # library_id: ~/.cache/ops/lib/ 下的命名空间键。历史上住在 sync 段;
        # sync 栈已退役 (JOURNAL F1),仅存此键。
        sync_cfg: dict[str, Any] = config.get("sync") or {}
        self.library_id: str = sync_cfg.get("library_id") or self.alpha_src.parent.name

        # state backend: postgres (生产真相源) | json (单机 dev/test)。
        # redis 后端已删除 —— 三表拆分后它与 FactorRecord 不兼容,作为"紧急
        # 回退"是假保险。承载它的 redis-sentinel 实例是 JFS metadata 后端,
        # 与 ops 无关,不受影响。
        state_cfg: dict[str, Any] = config.get("state") or {}
        self.state_backend: str = state_cfg.get("backend") or "json"

        # state.postgres backend (single source of truth). Password resolution:
        # postgres.password (literal) > password_env > password_file.
        state_pg_cfg: dict[str, Any] = state_cfg.get("postgres") or {}
        self.state_postgres_conninfo: str | None = self._build_pg_conninfo(state_pg_cfg)

        # 锁命名空间注入口 —— **仅测试用**。生产一律走 lock.py 的固定缺省
        # 'ops:factor_lock':锁键随 config 漂移会让跨机互斥无声失效,生产
        # config 绝不能设置本键。测试夹具把它设成本 session 的 PG schema 名,
        # 使并行 pytest 进程的 advisory lock 互不干扰(advisory lock 是库级
        # 作用域,schema 隔离挡不住它)。
        self.lock_namespace: str | None = state_cfg.get("lock_namespace")

        # (derived 层配置随僵尸层删除, JOURNAL V2:
        #  metrics/datasources/bcorr 在 factor_snapshot,index 缓存不复存在。)

    @staticmethod
    def _build_pg_conninfo(pg_cfg: dict[str, Any]) -> str | None:
        """Assemble a libpq conninfo string from state.postgres.* config.

        Returns None when no host/dbname is configured (backend stays json).
        Password resolves in the same 3-tier order as redis (literal / env / file).
        """
        if not pg_cfg:
            return None
        host = pg_cfg.get("host")
        dbname = pg_cfg.get("dbname")
        if not host or not dbname:
            return None
        pwd: str | None = pg_cfg.get("password")
        if pwd is None:
            env_var = pg_cfg.get("password_env") or "OPS_DERIVED_PG_PASSWORD"
            pwd = os.environ.get(env_var)
        if pwd is None:
            pwd_file = pg_cfg.get("password_file")
            pwd_key = pg_cfg.get("password_key", "OPS_PG_PASSWORD")
            if pwd_file:
                try:
                    with open(pwd_file) as f:
                        for line in f:
                            line = line.strip()
                            if line.startswith(f"{pwd_key}="):
                                pwd = line.split("=", 1)[1]
                                break
                except (PermissionError, OSError, FileNotFoundError):
                    pass
        parts = [
            f"host={host}",
            f"port={pg_cfg.get('port', 15432)}",
            f"dbname={dbname}",
            f"user={pg_cfg.get('user', 'ops')}",
        ]
        if pwd:
            parts.append(f"password={pwd}")
        # options 透传:libpq 命令行选项,如 `-csearch_path=t_xxx`(测试的
        # per-session schema 隔离用)。值不能含空格(conninfo 不做引号转义);
        # 生产 config 不设置本键。
        opts = pg_cfg.get("options")
        if opts:
            parts.append(f"options={opts}")
        return " ".join(parts)

    @staticmethod
    def _resolve_vars(
            raw: dict[str, Any], hostname: str | None = None,
    ) -> tuple[dict[str, Any], bool | None, list[str]]:
        """Resolve ${var_name} references in config values.

        变量优先级:
        **OPS_* 环境变量 > hosts[本机 hostname] > vars 基础值**。
        hosts 块按 hostname 精确匹配,覆盖 vars 同名项 —— 每台机器的挂载点
        差异进配置,同一份 config.yaml 四机零环境变量可用。

        返回 (resolved_raw, host_matched, env_overrides):host_matched 为
        None(无 hosts 块)/ False(有块未命中)/ True(命中);env_overrides
        是生效的 OPS_* 覆盖键列表 —— 供 `ops setup` 显性提示(env 优先是刻意
        的逃生口,但必须可见:残留旧 OPS_* 会静默压掉 hosts 声明)。
        """
        vars_block = raw.pop("vars", {})
        hosts_block = raw.pop("hosts", None)

        host_matched: bool | None = None
        if isinstance(hosts_block, dict):
            host_matched = False
            overrides = hosts_block.get(hostname) if hostname else None
            if isinstance(overrides, dict):
                vars_block.update(overrides)
                host_matched = True

        env_overrides: list[str] = []
        if not vars_block:
            return raw, host_matched, env_overrides

        # Environment variables override: OPS_GSIM_HOME -> gsim_home
        for key in vars_block:
            env_key = f"OPS_{key.upper()}"
            env_val = os.environ.get(env_key)
            if env_val:
                if str(vars_block[key]) != env_val:
                    env_overrides.append(env_key)
                vars_block[key] = env_val

        pattern = re.compile(r"\$\{(\w+)\}")

        def replace(val):
            if isinstance(val, str):
                return pattern.sub(lambda m: str(vars_block.get(m.group(1), m.group(0))), val)
            if isinstance(val, dict):
                return {k: replace(v) for k, v in val.items()}
            if isinstance(val, list):
                return [replace(v) for v in val]
            return val

        return replace(raw), host_matched, env_overrides  # type: ignore

    @staticmethod
    def load(config_path: Path) -> "Config":
        # 缺文件干净退出而非裸 FileNotFoundError 双 traceback:uv tool install
        # 从任意目录跑时三级解析(OPS_CONFIG → ./config.yaml → 项目根)可能全落空,
        # 报错必须自带怎么修。承重墙是 main.py 的 `except SystemExit: raise`
        # 专臂(别删它 —— 否则 BaseException 臂会把双 traceback 加回来);
        # sudo.maybe_elevate 的 except Exception 拦不住 SystemExit,写命令在
        # sudo prompt 之前就报错,不会白输密码。
        if not config_path.is_file():
            env = os.environ.get("OPS_CONFIG")
            if env and Path(env) == config_path:
                hint = f"OPS_CONFIG={env} 指向不存在的文件,修正或 unset 它"
            elif config_path != get_default_config_path():
                # 路径不是缺省解析给的 → 来自显式 -c/--config-path,
                # 别谎报"三级解析落空"误导用户去改 env
                hint = "路径来自 -c/--config-path,检查拼写"
            else:
                hint = ("解析顺序 OPS_CONFIG → ./config.yaml → 项目根 config.yaml 全部落空。\n"
                        "修法任选:  export OPS_CONFIG=/path/to/gsim-ops/config.yaml"
                        "(uv tool 安装的 ops 推荐,写进 ~/.bashrc)\n"
                        "         或  ops <子命令> -c /path/to/config.yaml\n"
                        "         或  cd 到 gsim-ops 仓库目录再跑")
            raise SystemExit(f"ops: 找不到配置文件 {config_path}\n{hint}")
        with config_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f.read())
        hostname = socket.gethostname()
        raw, host_matched, env_overrides = Config._resolve_vars(raw, hostname)
        config = Config(raw)
        config.hostname = hostname
        config.host_declared = host_matched
        config.env_overrides = env_overrides
        return config
