import re
import xmltodict
from pathlib import Path
from .key import AlphaKey


class AlphaMetadata:
    def __init__(self, user: str, date: str, factor_dir: Path):
        self.dir = factor_dir

        if not self.dir.exists():
            raise
        
        self.xml_file = list(self.dir.glob("*.xml"))[0]
        self.py_file = list(self.dir.glob("*.py"))[0]
        self.readme_file = None # TODO: readme

        with open(self.xml_file) as f:
            self.xml_config = xmltodict.parse(f.read())
        self.name: str = self.xml_config["gsim"]["Portfolio"]["Alpha"]["@id"]
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