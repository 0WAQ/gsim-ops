#!/usr/bin/env python3
import os
import shutil
import xmltodict
from pathlib import Path

from ...common.config import Config
from ...common.utils import date_range


def find_xml_files(root_path: Path, start: str, end: str) -> list[tuple[Path, Path]]:    
    xml_files = []
    for date_str in date_range(start, end):
        date_folder = root_path / date_str

        if not date_folder.exists():
            continue

        for factor_folder in date_folder.iterdir():
            if factor_folder.is_dir() and factor_folder.name.startswith("Alpha"):
                xml_file = list(factor_folder.glob("*.xml"))[0]
                xml_files.append((xml_file, factor_folder))

    return xml_files


def modify_xml_file(xml_path: Path, factor_path: Path):
    try:
        f = open(xml_path, 'r+', encoding='utf-8')
        
        root = xmltodict.parse(f.read())
        gsim = root['gsim']

        os.makedirs("/mnt/storage/work/wbai/alpha/dropbox/checkpoint", exist_ok=True)

        py_file = list(factor_path.glob("*.py"))[0]
        factor_name = gsim["Portfolio"]["Alpha"]["@id"]
        gsim['Modules']['Alpha']['@module'] = factor_path / py_file
        gsim['Portfolio']['Alpha']['@dumpAlphaFile'] = 'true'
        gsim['Portfolio']['Alpha']['@dumpAlphaDir'] = "/mnt/storage/work/wbai/alpha/dropbox/alpha"
        gsim['Portfolio']['Stats']['@pnlDir'] = "/mnt/storage/work/wbai/alpha/dropbox/pnl"
        gsim['Portfolio']['Stats']['@module'] = 'StatsLongShort'
        gsim['Constants']['@checkpointDays'] = '5'
        gsim['Constants']['@checkpointDir'] = f"/mnt/storage/work/wbai/alpha/dropbox/checkpoint/{factor_name}/"
        gsim['Constants']['@niodatapath'] = "/datasvc/data/cc2"

        # TODO:
        gsim['Universe']['@startdate'] = "20241220"
        gsim['Universe']['@enddate'] = "20241231"

        f.seek(0)
        f.write(xmltodict.unparse(root, pretty=True, encoding='utf-8', full_document=False))
        f.truncate()
    finally:
        f.close()

def do_main(users: list[str], start: str, end: str, config: Config):
    print(f"modifing xml files:")

    for user in users:
        src_dir = config.dropbox_path / user
        root_dir = config.dropbox_path_target / user
        os.makedirs(root_dir, exist_ok=True)

        for date_str in date_range(start, end):
            src_date_dir = src_dir / date_str
            if not src_date_dir.is_dir():
                continue
            dst_date_dir = root_dir / date_str
            if dst_date_dir.exists():
                shutil.rmtree(dst_date_dir)
            shutil.copytree(src_date_dir, dst_date_dir)

    xml_files = find_xml_files(root_dir, start, end)
    if not xml_files:
        print("  Can't find any xml file")
        return
    print(f"  Found {len(xml_files)} to modify:")

    success_count = 0
    error_count = 0
    for i, (xml_path, factor_path) in enumerate(xml_files, 1):
        try:
            modify_xml_file(xml_path, factor_path)
            success_count += 1
            if i % 10 == 0 or i == len(xml_files):
                print(f"  [{i}/{len(xml_files)}] ({i/len(xml_files)*100:.1f}%)")
        except KeyError as e:
            print(e)
            error_count += 1
        except Exception as e:
            error_count += 1
            print(f"{xml_path.relative_to(root_dir)}: {e}")

    
    if error_count > 0:
        print(f"  {success_count} succeed")
        print(f"  {error_count} failed")
    else:
        print(f"  All succeed")
    print("Finished")
