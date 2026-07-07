import xmltodict

from ops.core.alpha.metadata import AlphaMetadata
from ops.infra.config import Config


def prepare_for_initial(factor: AlphaMetadata, config: Config):
    nio_data_path = str(config.nio_data_path)

    factor._update_data_niodatapath(nio_data_path)
    factor.xml_config["gsim"]['Constants']['@niodatapath'] = nio_data_path
    factor.xml_config["gsim"]['Constants']['@checkpointDays'] = '5'
    factor.xml_config["gsim"]["Constants"]["@checkpointDir"] = str(config.checkpoint_path / factor.name) + "/"
    config.checkpoint_path.mkdir(parents=True, exist_ok=True)

    factor.xml_config["gsim"]['Modules']['Alpha']['@module'] = factor.py_file

    # TODO:
    factor.xml_config["gsim"]['Portfolio']['Stats']['@module'] = 'StatsSimpleV6'
    factor.xml_config["gsim"]['Portfolio']['Stats']['@mode'] = '0'
    factor.xml_config["gsim"]['Portfolio']['Alpha']['@dumpAlphaFile'] = 'true'
    factor.xml_config["gsim"]['Portfolio']['Alpha']['@dumpAlphaDir'] = str(config.alpha_path)
    factor.xml_config["gsim"]["Portfolio"]["Stats"]["@pnlDir"] = str(config.pnl_path)
    factor.xml_config["gsim"]["Portfolio"]["Stats"]["@dumpPnl"] = 'true'

    save_xml(factor)


def prepare_for_validate(factor: AlphaMetadata):
    try:
        factor.xml_config["gsim"]['Universe']['@startdate'] = "20241201"
        factor.xml_config["gsim"]['Universe']['@enddate'] = "20241202"
        factor.xml_config["gsim"]['Portfolio']['Stats']['@dumpPnl'] = 'false'
        factor.xml_config["gsim"]['Portfolio']['Alpha']['@dumpAlphaFile'] = 'false'
        save_xml(factor)
    except Exception:
        ...


def prepare_for_checkbias(factor: AlphaMetadata):
    try:
        factor.xml_config["gsim"]['Universe']['@startdate'] = "20241201"
        factor.xml_config["gsim"]['Universe']['@enddate'] = "20241231"
        factor.xml_config["gsim"]['Portfolio']['Stats']['@dumpPnl'] = 'true'
        factor.xml_config["gsim"]['Portfolio']['Alpha']['@dumpAlphaFile'] = 'true'
        save_xml(factor)
    except Exception:
        ...


def prepare_for_long_backtest(factor: AlphaMetadata):
    try:
        factor.xml_config["gsim"]["Universe"]["@startdate"] = "20150101"
        factor.xml_config["gsim"]["Universe"]["@enddate"]   = "20251231"
        factor.xml_config["gsim"]['Portfolio']['Stats']['@dumpPnl'] = 'true'
        save_xml(factor)
    except Exception:
        ...


def prepare_for_checkpoint(factor: AlphaMetadata):
    try:
        factor.xml_config["gsim"]['Portfolio']['Stats']['@dumpPnl'] = 'true'
        factor.xml_config["gsim"]['Portfolio']['Alpha']['@dumpAlphaFile'] = 'true'
        save_xml(factor)
    except Exception:
        ...


def prepare_for_compliance(factor: AlphaMetadata):
    ...


def prepare_for_correlation(factor: AlphaMetadata):
    ...


def prepare_for_archive(factor: AlphaMetadata):
    try:
        # TODO: 修改硬编码
        factor.xml_config["gsim"]["Modules"]["Alpha"]["@module"] = f"/mnt/storage/alphalib/alpha_src/{factor.name}/{factor.name}.py"
        factor.xml_config["gsim"]["Portfolio"]["Stats"]["@pnlDir"] = "/tmp/alphalib/alpha_pnl"
        factor.xml_config["gsim"]["Portfolio"]["Alpha"]["@dumpAlphaDir"] = "/tmp/alphalib/alpha_dump"
        save_xml(factor)
    except Exception:
        ...


def save_xml(factor: AlphaMetadata):
    with open(factor.xml_file, "r+") as f:
        f.write(xmltodict.unparse(factor.xml_config,
                                    pretty=True,
                                    encoding="utf-8",
                                    full_document=False))
        f.truncate()
