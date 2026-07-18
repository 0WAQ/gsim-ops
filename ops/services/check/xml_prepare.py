"""Stage 开跑前的 XML 改写:回测窗口 + dump 开关。

每个 prepare_* 只声明"这个 stage 需要什么窗口、开什么 dump",落盘走统一的
`_apply`。**prepare 失败直接抛**,不 `except Exception` 吞错:XML 缺键 /
写盘失败若被吞,stage 会拿着上一个 stage 的窗口继续跑 —— validate 可能跑成
全历史(30min+),checkbias 可能在错误区间做前视检查,结果全不可信。异常由
流水线 unexpected-error 臂接住(revert SUBMITTED + 完整日志),响亮失败取代
静默错跑。

prepare 与 stage 的绑定在 `stages.py` 的 PIPELINE 表(按引用绑定,非按名字)。
"""
from ops.core.alpha.metadata import AlphaMetadata
from ops.infra.config import Config
from ops.utils.xmlio import save_xml

# 回测窗口 (startdate, enddate) —— 全流水线的窗口只此三处定义
VALIDATE_WINDOW = ("20241201", "20241202")        # 最小可跑窗口:验证代码/环境能启动
CHECKBIAS_WINDOW = ("20241201", "20241231")       # 一个月:防火墙注入短回测
LONG_BACKTEST_WINDOW = ("20150101", "20251231")   # 全历史

# prepare_for_archive("拆雷":归档 XML 输出改指 /tmp)已于 2026-07-18 退役,
# 别加回来:归档 XML 现在**就是生产态**(repo.archive → core/prodxml 生产化,
# 入库即适配生产线,factor-produce-v3.md D9)。"防手动重跑砸库"的保护改由
# "库内因子不直跑 + 未来 ops export 导出"承担(plans.md TODO)。


def _apply(factor: AlphaMetadata, *,
           window: tuple[str, str] | None = None,
           dump_pnl: bool | None = None,
           dump_alpha: bool | None = None) -> None:
    """按声明改写 factor 的 XML 并落盘。None = 该项保持上个 stage 的值。"""
    gsim = factor.xml_config["gsim"]
    if window is not None:
        gsim["Universe"]["@startdate"], gsim["Universe"]["@enddate"] = window
    if dump_pnl is not None:
        gsim["Portfolio"]["Stats"]["@dumpPnl"] = "true" if dump_pnl else "false"
    if dump_alpha is not None:
        gsim["Portfolio"]["Alpha"]["@dumpAlphaFile"] = "true" if dump_alpha else "false"
    save_xml(factor.xml_file, factor.xml_config)


def prepare_for_validate(factor: AlphaMetadata) -> None:
    _apply(factor, window=VALIDATE_WINDOW, dump_pnl=False, dump_alpha=False)


def prepare_for_checkbias(factor: AlphaMetadata) -> None:
    _apply(factor, window=CHECKBIAS_WINDOW, dump_pnl=True, dump_alpha=True)


def prepare_for_checkpoint(factor: AlphaMetadata) -> None:
    _apply(factor, dump_pnl=True, dump_alpha=True)


def prepare_for_long_backtest(factor: AlphaMetadata) -> None:
    # dump_alpha 必须显式:compliance 的全史逐日判定吃的正是本 stage 产出的
    # dump,靠上一站(checkpoint)残留继承 = 改动别的 stage 会静默断供
    _apply(factor, window=LONG_BACKTEST_WINDOW, dump_pnl=True, dump_alpha=True)


def prepare_for_initial(factor: AlphaMetadata, config: Config) -> None:
    """流水线开跑前的一次性初始化(不属于任何 stage):niodatapath / checkpoint
    目录 / @module 指向 staging 内的 .py / dump 输出目录指向本库。"""
    nio_data_path = str(config.nio_data_path)

    factor._update_data_niodatapath(nio_data_path)
    gsim = factor.xml_config["gsim"]
    gsim['Constants']['@niodatapath'] = nio_data_path
    gsim['Constants']['@checkpointDays'] = '5'
    gsim["Constants"]["@checkpointDir"] = str(config.checkpoint_path / factor.name) + "/"
    config.checkpoint_path.mkdir(parents=True, exist_ok=True)

    gsim['Modules']['Alpha']['@module'] = factor.py_file

    # TODO: Stats 模块名硬编码
    gsim['Portfolio']['Stats']['@module'] = 'StatsSimpleV6'
    gsim['Portfolio']['Stats']['@mode'] = '0'
    gsim['Portfolio']['Alpha']['@dumpAlphaFile'] = 'true'
    gsim['Portfolio']['Alpha']['@dumpAlphaDir'] = str(config.alpha_path)
    gsim["Portfolio"]["Stats"]["@pnlDir"] = str(config.pnl_path)
    gsim["Portfolio"]["Stats"]["@dumpPnl"] = 'true'

    save_xml(factor.xml_file, factor.xml_config)


