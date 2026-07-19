"""分组生产:组划分与组 XML 生成(纯函数,SSOT 锚点见文末不变量)。

分组形态 = sibling `<Alpha>` 平铺共享一次 gsim init(日常提速的来源),dump/pnl
与 per-factor 位级一致(实证:`docs/remediation/BATCH-PRODUCE-MECHANICS-RESULT.md`)。

**四条不变量(施工红线,改这里先看实证)**:

1. 组的腿集合与顺序**永不修改** —— gsim checkpoint 按腿序号反序列化,加腿崩、
   删中间腿静默污染;允许的唯一编辑是单腿 `dumpAlphaFile` 属性翻转(静音)。
2. 组 XML **不引用 alpha_src 活代码** —— `@module` 一律指组内冻结副本
   (`code/<factor>/`),重入库替换 .py 不会热污染组 checkpoint。
3. 腿节点的属性与 `Operations`/`Description` 子树**整段照抄**源归档 XML,
   只覆盖 `dumpAlphaFile`/`dumpAlphaDir` —— delay/ndays/st 等私有属性是
   因子行为的一部分,重建会丢语义。
4. Data 声明按 `@id` 去重合并,**同 id 不同属性 = 冲突**,绝不静默选一个 ——
   两个因子读到不同数据却共用一个声明,产出会错得毫无声息。

无静态模板:Constants/Universe/Stats 骨架借自首因子归档 XML(生产化规则
`core/prodxml.py` 已把这些字段归一),模板若另立一份就是第二真相源。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ops.infra.config import Config

ONLY_DELAY = 1  # 当前只生产 delay1;delay0 归 jdw 盘中产线,不进组不进 pending


def as_list(node: Any) -> list:
    """xmltodict 单元素是 dict 不是 list —— 归一成 list 视图。"""
    if node is None:
        return []
    return node if isinstance(node, list) else [node]


@dataclass(frozen=True)
class GroupParams:
    """分组参数(正主 config `produce.grouped` 块)。"""
    root: str          # /nvme125/production/alpha
    group_size: int
    workers: int

    @classmethod
    def maybe_from_config(cls, config: Config) -> GroupParams | None:
        root = config.produce_grouped_root
        if root is None:
            return None
        return cls(root=str(root),
                   group_size=config.produce_grouped_size,
                   workers=config.produce_grouped_workers)

    # -- 派生路径(布局正主,别处不得手拼) --
    def group_dir(self, author: str, gid: str) -> str:
        return f"{self.root.rstrip('/')}/groups/{author}/delay{ONLY_DELAY}/{gid}"

    def code_dir(self, author: str, gid: str) -> str:
        return f"{self.group_dir(author, gid)}/code"

    def checkpoint_dir(self, author: str, gid: str) -> str:
        return f"{self.group_dir(author, gid)}/checkpoint/"

    @property
    def dump_root(self) -> str:
        return f"{self.root.rstrip('/')}/dump"

    @property
    def pnl_root(self) -> str:
        return f"{self.root.rstrip('/')}/pnl"

    @property
    def pending_checkpoint_root(self) -> str:
        return f"{self.root.rstrip('/')}/pending/checkpoint"

    @property
    def pending_log_root(self) -> str:
        return f"{self.root.rstrip('/')}/pending/logs"


# ---------------------------------------------------------------------------
# 组划分
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GroupSpec:
    """一组的划分结果(gid 由调用方发号,见 next_gid)。"""
    author: str
    delay: int
    members: tuple[str, ...]   # 字典序


def partition(records: list[tuple[str, str, int | None]],
              size: int) -> list[GroupSpec]:
    """(name, author, delay) → 分组。先滤 delay==ONLY_DELAY;按 author 聚合、
    作者内名字典序、按 size 切块。决定论:同输入同输出(重跑不会产生漂移分组)。"""
    by_author: dict[str, list[str]] = {}
    for name, author, delay in records:
        if delay != ONLY_DELAY:
            continue
        by_author.setdefault(author, []).append(name)
    specs: list[GroupSpec] = []
    for author in sorted(by_author):
        names = sorted(by_author[author])
        for i in range(0, len(names), size):
            specs.append(GroupSpec(author, ONLY_DELAY,
                                   tuple(names[i:i + size])))
    return specs


def next_gid(existing: set[str]) -> str:
    """发号:gNNN,取未占用的最小编号。组永不复号(重组 = 新 gid,旧组留痕)。"""
    n = 1
    while f"g{n:03d}" in existing:
        n += 1
    return f"g{n:03d}"


# ---------------------------------------------------------------------------
# 组 XML 生成
# ---------------------------------------------------------------------------

@dataclass
class MergeResult:
    """build_group_xml 的结果。conflicts 非空 = 该组不能封,dry-run 报告。"""
    gsim: dict[str, Any] | None = None
    conflicts: list[str] = field(default_factory=list)


def _frozen_module(node: dict, factor: str, code_dir: str) -> None:
    """Modules/Alpha 的 @module 改指冻结副本(basename 不动 —— SET-9 教训:
    目录名 ≠ .py 名的存量不少,文件名只能沿用原 basename)。"""
    mod = str(node.get("@module") or "")
    if mod.endswith(".py"):
        from pathlib import Path
        node["@module"] = f"{code_dir}/{factor}/{Path(mod).name}"


def build_group_xml(legs: list[tuple[str, dict]],
                    params: GroupParams, author: str, gid: str) -> MergeResult:
    """(factor_name, 归档 XML cfg dict) 列表 → 组 XML。

    骨架借首因子(Constants/Universe/Stats/Portfolio 属性),腿整段照抄各因子
    Portfolio/Alpha 子树。conflicts 非空时 gsim 为 None。
    """
    result = MergeResult()
    if not legs:
        result.conflicts.append("空组")
        return result

    skeleton = copy.deepcopy(legs[0][1])["gsim"]
    ref_universe = skeleton.get("Universe")

    data_by_id: dict[str, dict] = {}
    alpha_mods: list[dict] = []
    leg_nodes: list[tuple[str, dict]] = []
    code_dir = params.code_dir(author, gid)

    for name, cfg in legs:
        gsim = cfg["gsim"]
        if gsim.get("Universe") != ref_universe:
            result.conflicts.append(f"{name}: Universe 与首因子不一致")
        modules = gsim.get("Modules", {})
        for data in as_list(modules.get("Data")):
            did = str(data.get("@id"))
            if did in data_by_id and data_by_id[did] != data:
                result.conflicts.append(f"{name}: Data @id={did} 属性冲突")
            data_by_id.setdefault(did, data)
        for mod in as_list(modules.get("Alpha")):
            mod = copy.deepcopy(mod)
            _frozen_module(mod, name, code_dir)
            alpha_mods.append(mod)
        port_alpha = gsim.get("Portfolio", {}).get("Alpha")
        if port_alpha is None:
            result.conflicts.append(f"{name}: 归档 XML 缺 Portfolio/Alpha")
            continue
        leg = copy.deepcopy(port_alpha)
        leg["@dumpAlphaFile"] = "true"
        leg["@dumpAlphaDir"] = params.dump_root
        leg_nodes.append((name, leg))

    if result.conflicts:
        return result

    # 腿按因子名字典序 —— 顺序即 checkpoint 序号,排序在生成期一次定死
    leg_nodes.sort(key=lambda nl: nl[0])

    constants = skeleton["Constants"]
    constants["@checkpointDir"] = params.checkpoint_dir(author, gid)
    stats = skeleton["Portfolio"]["Stats"]
    stats["@pnlDir"] = params.pnl_root
    stats["@dumpPnl"] = "true"

    new_modules: dict[str, Any] = {}
    if data_by_id:
        new_modules["Data"] = list(data_by_id.values())
    if alpha_mods:
        new_modules["Alpha"] = alpha_mods

    result.gsim = {
        "gsim": {
            "Constants": constants,
            "Universe": ref_universe,
            "Modules": new_modules,
            "Portfolio": {
                **{k: v for k, v in skeleton["Portfolio"].items()
                   if k not in ("Alpha", "Stats")},
                "Stats": stats,
                "Alpha": [leg for _, leg in leg_nodes],
            },
        }
    }
    return result


# ---------------------------------------------------------------------------
# 组 XML 读取与静音(允许的唯一编辑)
# ---------------------------------------------------------------------------

def group_legs(cfg: dict) -> list[str]:
    """组 XML 的腿 id 序列(顺序 = checkpoint 序号)。"""
    return [str(a.get("@id"))
            for a in as_list(cfg["gsim"]["Portfolio"].get("Alpha"))]


def mute_legs(cfg: dict, names: set[str], mute: bool = True) -> list[str]:
    """翻转指定腿的 dumpAlphaFile(保序,属性级编辑)。返回实际翻转的腿。"""
    flipped: list[str] = []
    for a in as_list(cfg["gsim"]["Portfolio"].get("Alpha")):
        if str(a.get("@id")) in names:
            a["@dumpAlphaFile"] = "false" if mute else "true"
            flipped.append(str(a.get("@id")))
    return flipped
