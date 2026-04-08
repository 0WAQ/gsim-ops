"""Factor library scanner for querying factors in alphalib."""

import re
from pathlib import Path
from dataclasses import dataclass
from typing import Iterator

from .config import Config


@dataclass
class FactorInfo:
    """Factor metadata from library."""
    name: str
    author: str
    src_path: Path
    dump_path: Path
    pnl_path: Path
    has_pnl: bool
    dump_days: int


class LibraryScanner:
    """Scans factor library directories."""
    
    # Pattern: AlphaWbaiMomentum -> captures 'Wbai'
    AUTHOR_PATTERN = re.compile(r'^Alpha([A-Z][a-z]+)')
    
    def __init__(self, config: Config):
        self.config = config
        self.alpha_src = config.alpha_src
        self.alpha_dump = config.alpha_dump
        self.alpha_pnl = config.alpha_pnl
    
    @classmethod
    def from_config_path(cls, config_path: Path) -> "LibraryScanner":
        """Create scanner from config file path."""
        config = Config.load(config_path)
        return cls(config)
    
    def _parse_author(self, name: str) -> str:
        """Extract author from factor name (AlphaWbaiXxx -> wbai)."""
        match = self.AUTHOR_PATTERN.match(name)
        if match:
            return match.group(1).lower()
        return "unknown"
    
    def _count_dump_days(self, dump_path: Path) -> int:
        """Count number of dump days (v2.npy files)."""
        if not dump_path.exists():
            return 0
        
        count = 0
        try:
            for year_dir in dump_path.iterdir():
                if not year_dir.is_dir() or not re.match(r'^\d{4}$', year_dir.name):
                    continue
                for month_dir in year_dir.iterdir():
                    if not month_dir.is_dir() or not re.match(r'^\d{2}$', month_dir.name):
                        continue
                    count += len(list(month_dir.glob('*v2.npy')))
        except Exception:
            pass
        return count
    
    def _get_dump_date_range(self, dump_path: Path) -> tuple[str | None, str | None]:
        """Get first and last dump dates."""
        if not dump_path.exists():
            return None, None
        
        try:
            dates: list[str] = []
            for year_dir in sorted(dump_path.iterdir()):
                if not year_dir.is_dir() or not re.match(r'^\d{4}$', year_dir.name):
                    continue
                for month_dir in sorted(year_dir.iterdir()):
                    if not month_dir.is_dir() or not re.match(r'^\d{2}$', month_dir.name):
                        continue
                    for npy_file in month_dir.glob('*v2.npy'):
                        # filename: 20150105v2.npy
                        date_match = re.match(r'^(\d{8})v2\.npy$', npy_file.name)
                        if date_match:
                            dates.append(date_match.group(1))
            
            if dates:
                dates.sort()
                return dates[0], dates[-1]
        except Exception:
            pass
        return None, None
    
    def scan(self) -> list[FactorInfo]:
        """Scan alpha_src directory and return list of factors."""
        factors: list[FactorInfo] = []
        
        if not self.alpha_src.exists():
            return factors
        
        for factor_dir in sorted(self.alpha_src.iterdir()):
            if not factor_dir.is_dir():
                continue
            
            name = factor_dir.name
            author = self._parse_author(name)
            dump_path = self.alpha_dump / name
            pnl_path = self.alpha_pnl / name
            has_pnl = pnl_path.exists()
            dump_days = self._count_dump_days(dump_path)
            
            factors.append(FactorInfo(
                name=name,
                author=author,
                src_path=factor_dir,
                dump_path=dump_path,
                pnl_path=pnl_path,
                has_pnl=has_pnl,
                dump_days=dump_days,
            ))
        
        return factors
    
    def get(self, name: str) -> FactorInfo | None:
        """Get single factor by name."""
        src_path = self.alpha_src / name
        if not src_path.exists():
            return None
        
        dump_path = self.alpha_dump / name
        pnl_path = self.alpha_pnl / name
        
        return FactorInfo(
            name=name,
            author=self._parse_author(name),
            src_path=src_path,
            dump_path=dump_path,
            pnl_path=pnl_path,
            has_pnl=pnl_path.exists(),
            dump_days=self._count_dump_days(dump_path),
        )
    
    def get_dump_date_range(self, name: str) -> tuple[str | None, str | None]:
        """Get dump date range for a factor."""
        dump_path = self.alpha_dump / name
        return self._get_dump_date_range(dump_path)
    
    def filter_by_author(self, factors: list[FactorInfo], author: str) -> list[FactorInfo]:
        """Filter factors by author."""
        return [f for f in factors if f.author == author.lower()]
