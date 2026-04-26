import re
import xmltodict
from pathlib import Path
from .key import AlphaKey
from ops.infra.config import Config


class AlphaMetadata:
    def __init__(self, user: str, date: str, factor_dir: Path, config: Config):
        self.dir = factor_dir
        self.config = config

        if not self.dir.exists():
            raise

        self.xml_file = list(self.dir.glob("*.xml"))[0]
        self.py_file = list(self.dir.glob("*.py"))[0]
        self.readme_file = None # TODO: readme

        with open(self.xml_file) as f:
            self.xml_config = xmltodict.parse(f.read())

        self.name: str = self.xml_config["gsim"]["Portfolio"]["Alpha"]["@id"]
        self.delay: int = int(self.xml_config["gsim"]["Portfolio"]["Alpha"].get("@delay", 1))
        self._modify_always()

        self.start_date: str = self.xml_config["gsim"]["Universe"]["@startdate"]
        self.end_date: str = self.xml_config["gsim"]["Universe"]["@enddate"]
        self.checkpoint_days: str = self.xml_config["gsim"]["Constants"]["@checkpointDays"]

        self.key: AlphaKey = AlphaKey(user, date, self.name)

        pnl_dir = Path(self.xml_config["gsim"]["Portfolio"]["Stats"]["@pnlDir"])
        alpha_dir = Path(self.xml_config["gsim"]["Portfolio"]["Alpha"]["@dumpAlphaDir"])
        checkpoint_dir = Path(self.xml_config["gsim"]["Constants"]["@checkpointDir"])

        self.pnl_file = pnl_dir / self.name
        self.alpha_dir = alpha_dir / self.name
        self.checkpoint_dir = checkpoint_dir
        # TODO: other metadata

    def _modify_always(self):
        nio_data_path = str(self.config.nio_data_path)
        self.xml_config["gsim"]['Constants']['@niodatapath'] = nio_data_path
        self._update_data_niodatapath(nio_data_path)
        self.xml_config["gsim"]['Constants']['@checkpointDays'] = '5'
        self.xml_config["gsim"]["Constants"]["@checkpointDir"] = str(self.config.checkpoint_path / self.name) + "/"
        self.config.checkpoint_path.mkdir(parents=True, exist_ok=True)

        self.xml_config["gsim"]['Modules']['Alpha']['@module'] = self.py_file

        # TODO:
        self.xml_config["gsim"]['Portfolio']['Stats']['@module'] = 'StatsSimpleV5'
        self.xml_config["gsim"]['Portfolio']['Stats']['@mode'] = '0'
        self.xml_config["gsim"]['Portfolio']['Alpha']['@dumpAlphaFile'] = 'true'
        self.xml_config["gsim"]['Portfolio']['Alpha']['@dumpAlphaDir'] = str(self.config.alpha_path)
        self.xml_config["gsim"]["Portfolio"]["Stats"]["@pnlDir"] = str(self.config.pnl_path)
        self.xml_config["gsim"]["Portfolio"]["Stats"]["@dumpPnl"] = 'true'
        self.save()

    def _update_data_niodatapath(self, nio_data_path: str):
        modules = self.xml_config["gsim"]["Modules"]
        data_items = modules.get("Data", [])
        if isinstance(data_items, dict):
            data_items = [data_items]
        for item in data_items:
            old = item.get("@niodatapath")
            if old and old.startswith("/datasvc/data/cc/"):
                item["@niodatapath"] = nio_data_path + "/" + old[len("/datasvc/data/cc/"):]

    def parse(self):
        ...

    def save(self):
        with open(self.xml_file, "r+") as f:
            f.write(xmltodict.unparse(self.xml_config,
                                      pretty=True,
                                      encoding="utf-8",
                                      full_document=False))
            f.truncate()

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
        except Exception as e:
            ...
        return npy_files

    def get_last_v1npy_file(self) -> Path | None:
        try:
            last_year_dir = sorted(self.alpha_dir.glob('*'), reverse=True)[0]
            last_month_dir = sorted(last_year_dir.glob("*"), reverse=True)[0]
            last_v1npy_file = sorted(last_month_dir.glob("*v1.npy"), reverse=True)[0]
            return last_v1npy_file
        except Exception as e:
            return None

    def get_last_v2npy_file(self) -> Path | None:
        try:
            last_year_dir = sorted(self.alpha_dir.glob('*'), reverse=True)[0]
            last_month_dir = sorted(last_year_dir.glob("*"), reverse=True)[0]
            last_v2npy_file = sorted(last_month_dir.glob("*v2.npy"), reverse=True)[0]
            return last_v2npy_file
        except Exception as e:
            return None
