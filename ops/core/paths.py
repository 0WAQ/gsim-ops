"""因子盘面布局的唯一正主(SSOT)。

一个因子在因子库(alphalib)的全部落点从这里拼,**任何地方不得再手写
`config.alpha_xxx / name`** —— 散布各处手拼,"pnl 是单文件"这类布局事实就
只能靠文档人肉维持。

布局事实(由类型承载,不靠文档):
  - src / staging / dump      —— **目录**
  - pnl / 池副本 / feature    —— **单文件**(删除用 unlink,不能 rmtree!)
  - feature 命名 `<name>.<v1|v2>.npy`,meta.json 随因子目录走(staging 或 src)

边界:本类只管**因子库布局**(alphalib 五路径 + bcorr 池)。check 期工作区
(workspace 的 pnl_path/alpha_path/checkpoint_path)是 AlphaMetadata 的工作台
路径,不在此列。

归宿:FactorRepository 产物面的内部构件(`repo.paths(name)`);在那之前由各
service 直接 `FactorPaths.of(name, config)` 使用。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # 仅类型引用:core 不得运行期依赖 infra(import-linter)。
    from ops.infra.config import Config

# 因子目录身份证文件名(随因子在 staging → alpha_src 间迁移)
META_FILENAME = "meta.json"
# feature 的标准两版(pack 产出 <name>.v1.npy / <name>.v2.npy)
FEATURE_VERSIONS = ("v1", "v2")


@dataclass(frozen=True)
class FactorPaths:
    """一个因子的全部盘面落点。冻结、可 pickle(pack 的 ProcessPool worker 直传)。"""
    name: str
    src: Path              # alpha_src/<name>/        目录
    staging: Path          # staging/<name>/          目录
    dump: Path             # alpha_dump/<name>/       目录
    pnl: Path              # alpha_pnl/<name>         单文件 ⚠
    pool_automated: Path   # pnl_automated/<name>     单文件 ⚠(bcorr 分流池副本)
    pool_manual: Path      # pnl_manual/<name>        单文件 ⚠
    feature_dir: Path      # alpha_feature/           feature 所在目录(文件名见 feature())

    @classmethod
    def of(cls, name: str, config: Config) -> FactorPaths:
        return cls(
            name=name,
            src=config.alpha_src / name,
            staging=config.staging / name,
            dump=config.alpha_dump / name,
            pnl=config.alpha_pnl / name,
            pool_automated=config.pnl_automated / name,
            pool_manual=config.pnl_manual / name,
            feature_dir=config.alpha_feature,
        )

    def feature(self, version: str) -> Path:
        """alpha_feature/<name>.<version>.npy(单文件)。"""
        return self.feature_dir / f"{self.name}.{version}.npy"

    @property
    def features(self) -> tuple[Path, ...]:
        """标准两版 feature 候选路径(纯拼接,不查存在性 —— 调用方自行 exists)。"""
        return tuple(self.feature(v) for v in FEATURE_VERSIONS)

    @property
    def pools(self) -> tuple[Path, Path]:
        """两个 bcorr 分流池的副本路径。回收/删除时两个都查 —— 因子来源
        (discovery_method)可能在历史上变过。"""
        return (self.pool_automated, self.pool_manual)

    @property
    def src_meta(self) -> Path:
        return self.src / META_FILENAME

    @property
    def staging_meta(self) -> Path:
        return self.staging / META_FILENAME
