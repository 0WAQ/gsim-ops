
import xmltodict
from pathlib import Path

# TODO: 待统一
class AlphaXMLConfig:
    def __init__(self, xml_file: Path):
        with open(xml_file) as f:
            self.xml_config = xmltodict.parse(f.read())
        self.name: str = self.xml_config["gsim"]["Portfolio"]["Alpha"]["@id"]
        self.start_date: str = self.xml_config["gsim"]["Universe"]["@startdate"]
        self.end_date: str = self.xml_config["gsim"]["Universe"]["@enddate"]
        self.checkpoint_days: str = self.xml_config["gsim"]["Constants"]["@checkpointDays"]

        # self.key: AlphaKey = AlphaKey(user, date, self.name)

        pnl_dir = Path(self.xml_config["gsim"]["Portfolio"]["Stats"]["@pnlDir"])
        alpha_dir = Path(self.xml_config["gsim"]["Portfolio"]["Alpha"]["@dumpAlphaDir"])
        checkpoint_dir = Path(self.xml_config["gsim"]["Constants"]["@checkpointDir"])

        self.pnl_file = pnl_dir / self.name
        self.alpha_dir = alpha_dir / self.name
        self.checkpoint_dir = checkpoint_dir / self.name
