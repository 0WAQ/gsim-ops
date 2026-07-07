"""Stage 开跑前的 XML 改写:回测窗口 + dump 开关。

每个 prepare_* 只声明"这个 stage 需要什么窗口、开什么 dump",落盘走统一的
`_apply`。**prepare 失败直接抛**(2026-07-07 起):此前每个函数整段
`except Exception: ...` 吞错,XML 缺键 / 写盘失败时 stage 会拿着上一个 stage
的窗口继续跑 —— validate 可能跑成全历史(30min+),checkbias 可能在错误区间
做前视检查,结果全不可信。现在异常由流水线 unexpected-error 臂接住
(revert SUBMITTED + 完整日志),响亮失败取代静默错跑。

prepare 与 stage 的绑定在 `stages.py` 的 PIPELINE 表(按引用绑定,非按名字)。
"""
from ops.core.alpha.metadata import AlphaMetadata
from ops.infra.config import Config
from ops.utils.xmlio import save_xml

# 回测窗口 (startdate, enddate) —— 全流水线的窗口只此三处定义
VALIDATE_WINDOW = ("20241201", "20241202")        # 最小可跑窗口:验证代码/环境能启动
CHECKBIAS_WINDOW = ("20241201", "20241231")       # 一个月:防火墙注入短回测
LONG_BACKTEST_WINDOW = ("20150101", "20251231")   # 全历史

# 归档后的 XML 输出目录改指这里 —— 有人手动重跑入库因子的 config 时,
# pnl/dump 落 /tmp 而不是砸生产库
ARCHIVED_XML_SCRATCH = "/tmp/alphalib"


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
    _apply(factor, window=LONG_BACKTEST_WINDOW, dump_pnl=True)


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


def prepare_for_archive(factor: AlphaMetadata) -> None:
    """归档前"拆雷":pnl/dump 输出目录改指 /tmp,防手动重跑入库 XML 砸生产。

    @module 不在这里写 —— to_lib 搬完目录后 rewrite_module_path 是唯一权威
    (原先这里写死 /mnt/storage/alphalib 旧库路径、随后立刻被 rewrite 覆盖,
    属无效写入,已删)。
    """
    gsim = factor.xml_config["gsim"]
    gsim["Portfolio"]["Stats"]["@pnlDir"] = f"{ARCHIVED_XML_SCRATCH}/alpha_pnl"
    gsim["Portfolio"]["Alpha"]["@dumpAlphaDir"] = f"{ARCHIVED_XML_SCRATCH}/alpha_dump"
    save_xml(factor.xml_file, factor.xml_config)
