"""ops rm — 彻底删除一个因子(不可逆)。

删除因子的全部落点:
  - alpha_src/<name>/           源码目录 (唯一代码副本)
  - alpha_pnl/<name>            回测 PNL (单文件)
  - alpha_dump/<name>/          日频目标持仓目录
  - alpha_feature/<name>.*.npy  聚合 feature
  - factor_info PG 行           身份信息 (级联删除 state + snapshot)

没有软删/墓碑:因子被 rm 后即不存在 (要恢复只能重新 ops submit)。
默认交互确认展示完整删除清单;-y 跳过。
"""
import shutil

from ops.infra.config import Config
from ops.infra.lock import factor_lock, FactorLocked
from ops.infra.store import default_store
from ops.infra.info import default_info_store
from ops.utils.printer import banner, bottom, info, error, highlight


def _purge_artifacts(name: str, config: Config) -> list[str]:
    """删 alpha_dump/<name>/ + alpha_feature/<name>.{v}.npy。返回已删项。
    restage --purge 复用此函数,故与 src/pnl/state 删除分开。"""
    removed: list[str] = []
    dump_dir = config.alpha_dump / name
    if dump_dir.exists():
        shutil.rmtree(dump_dir)
        removed.append(f"alpha_dump/{name}")
    for v in ("v1", "v2"):
        f = config.alpha_feature / f"{name}.{v}.npy"
        if f.exists():
            f.unlink()
            removed.append(f"alpha_feature/{f.name}")
    return removed


def run_rm(args) -> None:
    name: str = args.factor_name
    config: Config = Config.load(args.config_path)
    store = default_store(config)

    rec = store.get(name)
    if rec is None:
        error(f"  ✘ 因子 {name} 不在 state 中")
        return

    banner(f"彻底删除因子 {name}")
    highlight(f"  状态: {rec.status.value}(删除后即不存在,不可逆)")
    info("  将删除以下全部落点:")
    info(f"    · alpha_src/{name}/          (源码,唯一代码副本)")
    info(f"    · alpha_pnl/{name}           (PNL)")
    info(f"    · alpha_dump/{name}/         (dump)")
    info(f"    · alpha_feature/{name}.*.npy (feature)")
    info(f"    · factor_info + 级联 (state + snapshot)")

    if not args.yes:
        ans = input("  确认彻底删除? 不可恢复 [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            info("  已取消")
            return

    try:
        with factor_lock(name, config):
            # 数据产物: dump + feature
            for r in _purge_artifacts(name, config):
                info(f"  ✔ 已删除 {r}")

            # 源码目录
            src_dir = config.alpha_src / name
            if src_dir.exists():
                shutil.rmtree(src_dir)
                info(f"  ✔ 已删除 alpha_src/{name}/")

            # PNL (单文件,不是目录)
            pnl = config.alpha_pnl / name
            if pnl.exists():
                pnl.unlink()
                info(f"  ✔ 已删除 alpha_pnl/{name}")

            # factor_info (级联删除 state + snapshot)
            if default_info_store(config).delete(name):
                info(f"  ✔ 已删除 factor_info (级联删除 state + snapshot)")
    except FactorLocked:
        error(f"  ✘ {name} 被另一个进程占用,稍后再试")
        return
    bottom()
