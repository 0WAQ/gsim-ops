"""归档生产化改写:因子 XML → 生产态(SSOT,docs/design/factor-produce-v3.md §4)。

因子入库后就直接适配生产线(拿来即用):本模块在**归档时**(repo.archive)与
**存量迁移**(scripts/migrate_prod_xml.py)对因子 XML 套三张声明式规则表,
执行顺序 SET → REPLACE → SUFFIX_STRIP(顺序有语义:REPLACE-① 先把旧路径形态
归一到 /datasvc,② 再统一迁移前缀)。产线驱动(ops produce)不再改写 XML。

纯函数:规则作用于 xmltodict 字典,参数经 ProdParams 注入(正主 config produce
块;core 不运行期依赖 infra)。所有规则**幂等**:生产化两次 ≡ 一次 —— 迁移脚本
可重跑、重入库归档不叠加。

坑位备忘(从现役产线的报错史固化,别"优化"掉):
- ★ REPLACE-② 跳过 <Universe>:secID/holidaysfile/calendarfile 指向 gsim 侧
  基础数据,必须保持 /datasvc;加前缀 → secpath 元数据不匹配 → 重建只读
  Universe 缓存 → PermissionError 崩。
- SUFFIX_STRIP 只对 <Data>/@id 削尾 Mod:上千个 Alpha id 以 Mod 结尾是命名
  惯例,全局子串替换会误伤。
- SET-9 @module 文件名沿用原 module 的 basename,不用目录名拼:目录名 ≠ .py
  名的存量不少,拼错 → gsim 回退 gsim.alpha 找属性 → AttributeError。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ops.utils.xmlio import load_xml, save_xml

if TYPE_CHECKING:
    from ops.infra.config import Config

# checkpoint 存档点距 enddate 的天数(gsim run_cp.py 缺省同值;续跑每日重写
# 尾部 ~5+N 天,即"尾部重写自愈"窗口)
CHECKPOINT_DAYS = "5"


@dataclass(frozen=True)
class ProdParams:
    """生产化参数(值最终写死进归档 XML;改 config 不溯及存量)。"""
    nio_data_path: str
    enddate: str
    startdate: str
    backdays: int
    checkpoint_root: str
    dump_root: str
    pnl_root: str
    datasvc_prefix: str
    module_prefix: str

    @classmethod
    def maybe_from_config(cls, config: Config) -> ProdParams | None:
        """produce 块整体缺失(dev/test 最小 config)→ None(调用方警告跳过);
        块存在但残缺 → from_config 的 ValueError(半配置是错误不是选项)。"""
        probe = (config.produce_nio_data_path, config.produce_startdate,
                 config.produce_checkpoint_root, config.produce_dump_root,
                 config.produce_pnl_root, config.produce_module_prefix)
        if all(v is None for v in probe):
            return None
        return cls.from_config(config)

    @classmethod
    def from_config(cls, config: Config) -> ProdParams:
        """config produce 块 → 参数。路径类键缺失响亮抛(缺配的生产化 = 把
        None 写进 XML 静默投产,比报错危险)。"""
        required = {
            "produce.nio_data_path": config.produce_nio_data_path,
            "produce.startdate": config.produce_startdate,
            "produce.checkpoint_root": config.produce_checkpoint_root,
            "produce.dump_root": config.produce_dump_root,
            "produce.pnl_root": config.produce_pnl_root,
            "produce.module_prefix": config.produce_module_prefix,
        }
        missing = [k for k, v in required.items() if v is None]
        if missing:
            raise ValueError(
                f"归档生产化缺 config 键: {', '.join(missing)} —— "
                "在 config.yaml 的 produce: 块补齐(参考 template/config.yaml)")
        return cls(
            nio_data_path=str(config.produce_nio_data_path),
            enddate=config.produce_enddate,
            startdate=str(config.produce_startdate),
            backdays=config.produce_backdays,
            checkpoint_root=str(config.produce_checkpoint_root),
            dump_root=str(config.produce_dump_root),
            pnl_root=str(config.produce_pnl_root),
            datasvc_prefix=config.produce_datasvc_prefix,
            module_prefix=str(config.produce_module_prefix),
        )


# ---------------------------------------------------------------------------
# 通用走查
# ---------------------------------------------------------------------------

def _as_list(node: Any) -> list:
    """xmltodict 单元素是 dict 不是 list —— 归一成 list 视图。"""
    if node is None:
        return []
    return node if isinstance(node, list) else [node]


def _walk_attrs(node: Any, fn) -> None:
    """递归遍历 dict/list,对每个 (@attr, str 值) 调 fn(dict, attr) 就地改写。"""
    if isinstance(node, dict):
        for k, v in node.items():
            if k.startswith("@") and isinstance(v, str):
                fn(node, k)
            else:
                _walk_attrs(v, fn)
    elif isinstance(node, list):
        for item in node:
            _walk_attrs(item, fn)


# ---------------------------------------------------------------------------
# SET:定位节点,强制设值
# ---------------------------------------------------------------------------

def _alpha_module_node(gsim: dict, name: str) -> dict | None:
    """定位"因子自身 .py"的 <Modules>/<Alpha> 节点:优先 @module basename ==
    <name>.py,否则第一个以 .py 结尾的;单节点直接取。避免误改 Data 的 Dmgr*.py
    (那些在 <Data> 标签下,不在 Modules.Alpha)。"""
    candidates = _as_list(gsim.get("Modules", {}).get("Alpha"))
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    py_nodes = [n for n in candidates
                if str(n.get("@module", "")).endswith(".py")]
    for n in py_nodes:
        if Path(str(n.get("@module"))).name == f"{name}.py":
            return n
    return py_nodes[0] if py_nodes else candidates[0]


def _apply_set(gsim: dict, name: str, p: ProdParams) -> None:
    ck = f"{p.checkpoint_root.rstrip('/')}/{name}/"
    gsim["Constants"]["@niodatapath"] = p.nio_data_path
    gsim["Constants"]["@backdays"] = str(p.backdays)
    gsim["Constants"]["@checkpointDir"] = ck
    gsim["Constants"]["@checkpointDays"] = CHECKPOINT_DAYS
    gsim["Universe"]["@startdate"] = p.startdate
    gsim["Universe"]["@enddate"] = p.enddate
    gsim["Portfolio"]["Stats"]["@pnlDir"] = p.pnl_root
    gsim["Portfolio"]["Stats"]["@dumpPnl"] = "true"
    gsim["Portfolio"]["Alpha"]["@dumpAlphaFile"] = "true"
    gsim["Portfolio"]["Alpha"]["@dumpAlphaDir"] = p.dump_root

    node = _alpha_module_node(gsim, name)
    if node is not None:
        orig = str(node.get("@module") or "")
        basename = Path(orig).name if orig.endswith(".py") else f"{name}.py"
        node["@module"] = f"{p.module_prefix.rstrip('/')}/{name}/{basename}"


# ---------------------------------------------------------------------------
# REPLACE:① 旧形态归一 → ② /datasvc 前缀迁移(跳过 Universe)→ ③ Stats 升级
# ---------------------------------------------------------------------------

# ①:(属性名, old, new, 前缀匹配?)。old 均为提交者旧世界的路径形态。
_LEGACY_RULES: tuple[tuple[str, str, str, bool], ...] = (
    ("@niodatapath", "/datasvc/data/cc_2025", "/datasvc/data/cc_all", True),
    ("@niodatapath", "/cache/data", "/datasvc/data", True),
    ("@niodatapath", "/home/fguo/data_local",
     "/datasvc/data/cc_all/cn_equity_feature_5min", False),
    ("@dataPath", "/home/fguo/data_local",
     "/datasvc/data/cc_all/cn_equity_feature_5min", False),
)


def _apply_replace(gsim: dict, p: ProdParams) -> None:
    def legacy(node: dict, attr: str) -> None:
        for rule_attr, old, new, prefix in _LEGACY_RULES:
            if attr != rule_attr:
                continue
            val = node[attr]
            if prefix:
                if val.startswith(old):
                    node[attr] = new + val[len(old):]
            elif old in val:
                node[attr] = val.replace(old, new)

    def migrate(node: dict, attr: str) -> None:
        if node[attr].startswith("/datasvc"):
            node[attr] = p.datasvc_prefix + node[attr]

    def stats_upgrade(node: dict, attr: str) -> None:
        if attr == "@module" and "StatsSimpleV5" in node[attr]:
            node[attr] = node[attr].replace("StatsSimpleV5", "StatsSimpleV6")

    _walk_attrs(gsim, legacy)
    # ★ Universe 例外:整棵子树跳过前缀迁移(见 module docstring 坑位备忘)
    for key, sub in gsim.items():
        if key == "Universe":
            continue
        _walk_attrs(sub, migrate)
    _walk_attrs(gsim, stats_upgrade)


# ---------------------------------------------------------------------------
# SUFFIX_STRIP:<Data>/@id 削尾 Mod
# ---------------------------------------------------------------------------

def _apply_strip(gsim: dict) -> None:
    for data in _as_list(gsim.get("Modules", {}).get("Data")):
        did = data.get("@id")
        if isinstance(did, str) and did.endswith("Mod"):
            data["@id"] = did[: -len("Mod")]


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def productionize(cfg: dict, *, name: str, params: ProdParams) -> None:
    """就地把因子 XML 字典改写为生产态(幂等)。cfg 须含顶层 'gsim'。
    缺 Constants/Universe/Portfolio 等承重节点直接 KeyError 冒泡 —— 静默跳过
    会归档出一个半生产态 XML。"""
    gsim = cfg["gsim"]
    _apply_set(gsim, name, params)
    _apply_replace(gsim, params)
    _apply_strip(gsim)


def productionize_file(xml_file: Path, *, name: str, params: ProdParams) -> None:
    cfg = load_xml(xml_file)
    productionize(cfg, name=name, params=params)
    save_xml(xml_file, cfg)
