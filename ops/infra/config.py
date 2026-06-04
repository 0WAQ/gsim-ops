import os
import re
import yaml
from pathlib import Path
from typing import Dict, Any


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
    2. ./config.prod.yaml (current directory)
    3. {project_root}/config.prod.yaml
    """
    # 1. Environment variable
    env_config = os.environ.get("OPS_CONFIG")
    if env_config:
        env_path = Path(env_config)
        if env_path.exists():
            return env_path

    # 2. Current directory
    cwd_config = Path.cwd() / "config.prod.yaml"
    if cwd_config.exists():
        return cwd_config

    # 3. Project root
    project_config = get_project_root() / "config.prod.yaml"
    if project_config.exists():
        return project_config

    # Fallback to project root (even if not exists, for error message)
    return project_config


class Config:
    def __init__(self, config: Dict[str, Any]):
        # checker
        self.compliance: Dict[str, Any] = config["checker"]["compliance"]
        self.correlation: Dict[str, Any] = config["checker"]["correlation"]
        self.checkpoint: Dict[str, Any] = config["checker"]["checkpoint"]

        # path
        self.dropbox_path = Path(config["path"]["dropbox_path"])
        self.pnl_prod_path = Path(config["path"]["pnl_prod_path"])
        self.pnl_pool_path = Path(config["path"]["pnl_pool_path"])
        self.pnl_alphalib = Path(config["path"]["pnl_alphalib"])
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

        # sync (optional)
        sync_cfg: Dict[str, Any] = config.get("sync") or {}
        self.sync_remote: str | None = sync_cfg.get("remote")
        self.library_id: str = sync_cfg.get("library_id") or self.alpha_src.parent.name

        # sync.s3 (optional — if present, use boto3 instead of rclone)
        s3_cfg: Dict[str, Any] = sync_cfg.get("s3") or {}
        self.s3_endpoint_url: str | None = s3_cfg.get("endpoint_url")
        self.s3_access_key_id: str | None = s3_cfg.get("access_key_id")
        self.s3_secret_access_key: str | None = s3_cfg.get("secret_access_key")
        self.s3_bucket: str | None = s3_cfg.get("bucket")

        # state backend (optional, default JSON file in ~/.cache/ops/lib/<library_id>/)
        # set state.backend: redis + state.redis.{url, password_env|password} to use the
        # multi-node redis store. PoC -- see scripts/juicefs-poc/06-redis-jfs.sh
        state_cfg: Dict[str, Any] = config.get("state") or {}
        self.state_backend: str = state_cfg.get("backend") or "json"
        redis_cfg: Dict[str, Any] = state_cfg.get("redis") or {}
        self.state_redis_url: str | None = redis_cfg.get("url")
        # password resolution order: password (literal) > password_env > $OPS_STATE_REDIS_PASSWORD
        pwd: str | None = redis_cfg.get("password")
        if pwd is None:
            env_var = redis_cfg.get("password_env") or "OPS_STATE_REDIS_PASSWORD"
            pwd = os.environ.get(env_var)
        self.state_redis_password: str | None = pwd

    @staticmethod
    def _resolve_vars(raw: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve ${var_name} references in config values.

        Variables are defined in the 'vars' block and can be overridden
        by environment variables with OPS_ prefix (e.g. OPS_GSIM_HOME).
        """
        vars_block = raw.pop("vars", {})
        if not vars_block:
            return raw

        # Environment variables override: OPS_GSIM_HOME -> gsim_home
        for key in vars_block:
            env_key = f"OPS_{key.upper()}"
            env_val = os.environ.get(env_key)
            if env_val:
                vars_block[key] = env_val

        pattern = re.compile(r"\$\{(\w+)\}")

        def replace(val):
            if isinstance(val, str):
                return pattern.sub(lambda m: vars_block.get(m.group(1), m.group(0)), val)
            if isinstance(val, dict):
                return {k: replace(v) for k, v in val.items()}
            if isinstance(val, list):
                return [replace(v) for v in val]
            return val

        return replace(raw) # type: ignore

    @staticmethod
    def load(config_path: Path) -> "Config":
        with config_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f.read())
        raw = Config._resolve_vars(raw)
        return Config(raw)
