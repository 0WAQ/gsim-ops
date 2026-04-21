from typing import Dict
from pathlib import Path
from .key import AlphaKey
from .metadata import AlphaMetadata
from ops.infra.config import Config
from ops.utils.func import date_range


class AlphaMetadatas:
    def __init__(self, dropbox_path_target: Path, users: list[str], start: str, end: str, config: Config, factor: str | None=None):
        self.dropbox_path_target = dropbox_path_target
        self.users = users
        self.start = start
        self.end = end

        self._flat: Dict[AlphaKey, AlphaMetadata] = {}
        if factor:
            assert start == end, "start must equal to end"
            for user in users:
                factor_dir = dropbox_path_target / user / start / factor
                if not factor_dir.exists() and not factor_dir.is_dir():
                    assert "factor wrong"
                try:
                    md = AlphaMetadata(user, start, factor_dir, config)
                    self._flat[md.key] = md
                except Exception as e:
                    ...
        else:
            for user in users:
                root_dir = dropbox_path_target / user
                for date in date_range(start, end):
                    date_path = root_dir / date
                    if not date_path.exists() or not date_path.is_dir():
                        continue
                    for factor_dir in date_path.iterdir():
                        if not factor_dir.name.startswith("Alpha"):
                            continue

                        try:
                            md = AlphaMetadata(user, date, factor_dir, config)
                            self._flat[md.key] = md
                        except Exception as e: # TODO: diy exception
                            ...

    def keys(self):
        return self._flat.keys()

    def values(self):
        return self._flat.values()

    def __getitem__(self, key: AlphaKey):

        return self._flat[key]

    def __setitem__(self, key: AlphaKey, value: AlphaMetadata):
        self._flat[key] = value

    def __len__(self):
        return len(self._flat)

    def __iter__(self):
        for _, md in self._flat.items():
            yield md
