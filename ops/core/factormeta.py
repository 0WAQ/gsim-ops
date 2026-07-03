from dataclasses import dataclass, field, asdict
from pathlib import Path
import json


META_VERSION = 1


@dataclass
class FactorMeta:
    name: str
    author: str
    birthday: int
    universe: str
    category: str
    delay: int

    backdays: int
    dump_alpha: bool
    has_intraday_curve: bool

    operations: list[dict] = field(default_factory=list)
    declared_data_modules: list[str] = field(default_factory=list)

    datasources: dict = field(default_factory=lambda: {"fields": [], "tables": []})
    code_lines: int = 0

    frequency: str = "daily"
    discovery_method: str | None = None

    submitted_at: str | None = None
    submitted_by: str | None = None
    meta_version: int = META_VERSION

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False))

    @classmethod
    def load(cls, path: Path) -> "FactorMeta":
        return cls(**json.loads(path.read_text()))
