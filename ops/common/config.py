import yaml
from pathlib import Path
from typing import Dict, Any

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
        
        # authors:  # TODO:
        self.authors: dict[str, dict[str, str]] = config["authors"]
        self.summary_emails: dict[str, list[str]] = config["notification"]["summary_emails"]
        self.send_author_email: bool = bool(config["notification"]["send_author_email"])

        # mode
        self.max_workers: int = config["mode"]["max_workers"]
        self.dry_run: bool = config["mode"]["dry_run"]
        self.timeout: int = config["mode"]["timeout"]

    @staticmethod
    def load(config_path: Path) -> "Config":
        with config_path.open('r', encoding="utf-8") as f:
            raw = yaml.safe_load(f.read())
        return Config(raw)