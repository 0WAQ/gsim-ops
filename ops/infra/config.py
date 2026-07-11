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
    config.prod-legacy.yaml 已于 2026-07-07 Wave 1 删除 (假保险)。
    """
    # 1. Environment variable
    env_config = os.environ.get("OPS_CONFIG")
    if env_config:
        env_path = Path(env_config)
        if env_path.exists():
            return env_path

    # 2. Current directory
    cwd_config = Path.cwd() / "config.yaml"
    if cwd_config.exists():
        return cwd_config

    # 3. Project root
    project_config = get_project_root() / "config.yaml"
    if project_config.exists():
        return project_config

    # Fallback to project root (even if not exists, for error message)
    return project_config


class Config:
    def __init__(self, config: dict[str, Any]):
        # hosts 声明命中情况(load() 回填;直接构造 Config(raw) 的路径保持缺省。
        # ops setup 的 host-declared 检查项消费)
        self.hostname: str = ""
        self.host_declared: bool | None = None

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

        # library_id: ~/.cache/ops/lib/ 下的命名空间键。历史上住在 sync 段;
        # sync 栈已于 2026-07-07 退役 (Wave 1, JOURNAL F1),仅存此键
        # (G-wave 时迁到顶层)。
        sync_cfg: dict[str, Any] = config.get("sync") or {}
        self.library_id: str = sync_cfg.get("library_id") or self.alpha_src.parent.name

        # state backend: postgres (生产真相源) | json (单机 dev/test)。
        # 2026-07-07 Wave 1: redis 后端删除 —— 三表拆分后它与 FactorRecord 不
        # 兼容,作为"紧急回退"是假保险 (full-review P0-2/G1)。承载它的
        # redis-sentinel 实例是 JFS metadata 后端,与 ops 无关,不受影响。
        state_cfg: dict[str, Any] = config.get("state") or {}
        self.state_backend: str = state_cfg.get("backend") or "json"

        # state.postgres backend (single source of truth, migrated from redis
        # 2026-07-04). Password resolution:
        # postgres.password (literal) > password_env > password_file.
        state_pg_cfg: dict[str, Any] = state_cfg.get("postgres") or {}
        self.state_postgres_conninfo: str | None = self._build_pg_conninfo(state_pg_cfg)

        # (derived 层配置随僵尸层删除, 2026-07-07 Wave 2, JOURNAL V2:
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
        return " ".join(parts)

    @staticmethod
    def _resolve_vars(raw: dict[str, Any],
                      hostname: str | None = None) -> tuple[dict[str, Any], bool | None]:
        """Resolve ${var_name} references in config values.

        变量优先级(2026-07-11 hosts 声明,ops setup 配套):
        **OPS_* 环境变量 > hosts[本机 hostname] > vars 基础值**。
        hosts 块按 hostname 精确匹配,覆盖 vars 同名项 —— 每台机器的挂载点
        差异进配置,同一份 config.yaml 四机零环境变量可用。

        返回 (resolved_raw, host_matched):host_matched 为 None(无 hosts 块)/
        False(有块未命中)/ True(命中),供 `ops setup` 报告路径来源。
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

        if not vars_block:
            return raw, host_matched

        # Environment variables override: OPS_GSIM_HOME -> gsim_home
        for key in vars_block:
            env_key = f"OPS_{key.upper()}"
            env_val = os.environ.get(env_key)
            if env_val:
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

        return replace(raw), host_matched  # type: ignore

    @staticmethod
    def load(config_path: Path) -> "Config":
        with config_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f.read())
        hostname = socket.gethostname()
        raw, host_matched = Config._resolve_vars(raw, hostname)
        config = Config(raw)
        config.hostname = hostname
        config.host_declared = host_matched
        return config
