"""Normalize factor naming in the XML so it matches the directory name.

Factor directory name convention: `Alpha{User}{Xxx}` (e.g. `AlphaFguo20260403GA005`).
  - `Alpha` is a fixed prefix
  - `{User}` is the author (lowercase tail, starts with uppercase)
  - `{Xxx}` is the actual factor identifier (e.g. `20260403GA005`)

Filename conventions:
  - `.py` filename matches the full dir name: `AlphaFguo20260403GA005.py`
  - `.xml` filename only needs to contain `{Xxx}`: `Config.Fguo.20260403GA005.xml`

Rules (auto-fixed in-place on staging XML):
  1. Portfolio.Alpha.@id   → {dir_name}
  2. Modules.Alpha.@id     → {dir_name}Mod
  3. Modules.Alpha.@module → path with stem {dir_name}

The .py / .xml file basenames are NOT renamed (would break imports). A warning
is printed if their stems don't carry the expected identifier.
"""

import xmltodict
from pathlib import Path

from ops.utils.printer import info, warn


MOD_SUFFIX = "Mod"


def _extract_xxx(factor_name: str) -> str:
    """`AlphaFguo20260403GA005` → `20260403GA005`. Strips `Alpha` prefix and author segment.

    Author segment is one uppercase letter followed by any number of lowercase
    letters (e.g. `Fguo`, `Wbai`, `Jzhang`).
    """
    name = factor_name
    if name.startswith("Alpha"):
        name = name[5:]
    if name and name[0].isupper():
        i = 1
        while i < len(name) and name[i].islower():
            i += 1
        name = name[i:]
    return name


def _save(xml_file: Path, cfg: dict) -> None:
    xml_file.write_text(
        xmltodict.unparse(cfg, pretty=True, encoding="utf-8", full_document=False)
    )


def normalize_factor_xml(staging_dir: Path) -> None:
    """Inspect the factor's XML and rewrite mismatched name fields in-place."""
    factor_name = staging_dir.name
    xxx = _extract_xxx(factor_name)
    xml_files = list(staging_dir.glob("*.xml"))
    py_files = list(staging_dir.glob("*.py"))
    if not xml_files:
        return
    xml_file = xml_files[0]

    cfg = xmltodict.parse(xml_file.read_text(encoding="utf-8"))

    portfolio_alpha = cfg.get("gsim", {}).get("Portfolio", {}).get("Alpha")
    modules_alpha = cfg.get("gsim", {}).get("Modules", {}).get("Alpha")

    changed: list[str] = []

    want_mod_id = factor_name + MOD_SUFFIX

    if isinstance(portfolio_alpha, dict):
        cur = portfolio_alpha.get("@id")
        if cur != factor_name:
            portfolio_alpha["@id"] = factor_name
            changed.append(f"Portfolio.Alpha.@id: {cur!r} → {factor_name!r}")

        cur_pmod = portfolio_alpha.get("@module")
        if cur_pmod != want_mod_id:
            portfolio_alpha["@module"] = want_mod_id
            changed.append(f"Portfolio.Alpha.@module: {cur_pmod!r} → {want_mod_id!r}")

    if isinstance(modules_alpha, dict):
        cur_id = modules_alpha.get("@id")
        if cur_id != want_mod_id:
            modules_alpha["@id"] = want_mod_id
            changed.append(f"Modules.Alpha.@id: {cur_id!r} → {want_mod_id!r}")

        cur_mod = modules_alpha.get("@module")
        if cur_mod:
            cur_stem = Path(str(cur_mod)).stem
            if cur_stem != factor_name:
                p = Path(str(cur_mod))
                new_path = str(p.with_name(f"{factor_name}.py"))
                modules_alpha["@module"] = new_path
                changed.append(f"Modules.Alpha.@module stem: {cur_stem!r} → {factor_name!r}")

    if changed:
        _save(xml_file, cfg)
        info(f"  ⚙  {factor_name} auto-fixed XML:")
        for c in changed:
            info(f"       - {c}")

    if py_files and py_files[0].stem != factor_name:
        warn(f"  ⚠  {factor_name}: .py filename ({py_files[0].name}) should be {factor_name}.py")
    if xml_files and xxx not in xml_files[0].name:
        warn(f"  ⚠  {factor_name}: .xml filename ({xml_files[0].name}) does not contain {xxx!r}")
