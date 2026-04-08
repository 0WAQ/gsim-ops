import os
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
    2. ./config.yaml (current directory)
    3. {project_root}/config.yaml
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
    def __init__(self, config: Dict[str, Any]):
        # checker
        self.compliance: Dict[str, Any] = config["checker"]["compliance"]
        self.correlation: Dict[str, Any] = config["checker"]["correlation"]
        self.checkpoint: Dict[str, Any] = config["checker"]["checkpoint"]

        # path
        self.dropbox_path = Path(config["path"]["dropbox_path"])
        self.dropbox_path_target = Path(config["path"]["dropbox_path_target"])
        self.pnl_prod_path = Path(config["path"]["pnl_prod_path"])
        self.pnl_pool_path = Path(config["path"]["pnl_pool_path"])
        self.pnl_alphalib = Path(config["path"]["pnl_alphalib"])
        self.python_path = Path(config["path"]["python_path"])

        self.alpha_src = Path(config["path"]["alpha_src"])
        self.alpha_dump = Path(config["path"]["alpha_dump"])
        self.alpha_pnl = Path(config["path"]["alpha_pnl"])
        self.recycle = Path(config["path"]["recycle"])

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

    @staticmethod
    def load(config_path: Path) -> "Config":
        with config_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f.read())
        return Config(raw)
