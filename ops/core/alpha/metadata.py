from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from ops.utils.xmlio import load_xml

from .key import AlphaKey

if TYPE_CHECKING:
    # 仅类型引用:core 不得运行期依赖 infra(import-linter C1)。Config 实例
    # 由调用方传入,这里只消费其路径属性。
    from ops.infra.config import Config


class AlphaMetadata:
    def __init__(self, user: str, date: str, factor_dir: Path, config: Config):
        self.dir = factor_dir

        if not self.dir.exists():
            raise Exception(f"no factor: {user}/{date}/{factor_dir}")

        self.xml_file = list(self.dir.glob("*.xml"))[0]
        self.py_file = list(self.dir.glob("*.py"))[0]

        self.xml_config = load_xml(self.xml_file)

        # TODO: name之后要依赖submit后的meta
        self.name: str = self.xml_config["gsim"]["Portfolio"]["Alpha"]["@id"]
        self.key: AlphaKey = AlphaKey(user, date, self.name)

        self.delay = int(self.xml_config["gsim"]["Portfolio"]["Alpha"].get("@delay", 1))
        desc = self.xml_config["gsim"]["Portfolio"]["Alpha"].get("Description", {}) or {}
        self.discovery_method = desc.get("@discovery_method")
        self.start_date: str = self.xml_config["gsim"]["Universe"]["@startdate"]
        self.end_date: str = self.xml_config["gsim"]["Universe"]["@enddate"]
        self.pnl_file = config.pnl_path / self.name
        self.alpha_dir = config.alpha_path / self.name
        self.checkpoint_dir = config.checkpoint_path / self.name

    def _update_data_niodatapath(self, nio_data_path: str):
        modules = self.xml_config["gsim"]["Modules"]
        data_items = modules.get("Data", [])
        if isinstance(data_items, dict):
            data_items = [data_items]
        for item in data_items:
            old = item.get("@niodatapath")
            if old and old.startswith("/datasvc/data/cc/"):
                item["@niodatapath"] = nio_data_path + "/" + old[len("/datasvc/data/cc/"):]

    def get_v2npy_files(self) -> list[Path]:
        npy_files: list[Path] = []
        try:
            for year in sorted(self.alpha_dir.glob("*")):
                if not year.is_dir() or not re.match(r"^\d{4}$", year.name):
                    continue

                for month in sorted(year.glob("*")):
                    if not month.is_dir() or not re.match(r"^\d{2}$", month.name):
                        continue
                    for npy_file in sorted(month.glob("*v2.npy")):
                        npy_files.append(npy_file)
        except Exception:
            ...
        return npy_files

    def get_last_v1npy_file(self) -> Path | None:
        try:
            last_year_dir = sorted(self.alpha_dir.glob('*'), reverse=True)[0]
            last_month_dir = sorted(last_year_dir.glob("*"), reverse=True)[0]
            last_v1npy_file = sorted(last_month_dir.glob("*v1.npy"), reverse=True)[0]
            return last_v1npy_file
        except Exception:
            return None

    def get_last_v2npy_file(self) -> Path | None:
        try:
            last_year_dir = sorted(self.alpha_dir.glob('*'), reverse=True)[0]
            last_month_dir = sorted(last_year_dir.glob("*"), reverse=True)[0]
            last_v2npy_file = sorted(last_month_dir.glob("*v2.npy"), reverse=True)[0]
            return last_v2npy_file
        except Exception:
            return None
