"""ops rm — 彻底删除一个因子(不可逆)。

删除因子的全部落点:
  - alpha_src/<name>/           源码目录 (唯一代码副本)
  - alpha_pnl/<name>            回测 PNL (单文件)
  - alpha_dump/<name>/          日频目标持仓目录
  - alpha_feature/<name>.*.npy  聚合 feature
  - pnl_automated|pnl_manual/<name>  bcorr 分流池副本 (to_lib 写入;不清则已删
    因子的 pnl 永远留在对比池里参与后续因子的 bcorr,生产验证 L3-7 实测泄漏)
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


def _recycle_check_artifacts(name: str, config: Config) -> list[str]:
    """删 check 面产物:alpha_pnl/<name> + bcorr 池副本(均单文件)。返回已删项。

    rm / restage / submit --overwrite 共用:pnl 与池副本喂 correlation 的
    对比池和竞品指标,因子**离库即失效** —— 留着就是"自鬼影":重检时新 pnl
    对自己旧 pnl corr≈1,高相关分支要求打败几乎相同的自己 → 必拒;也让别的
    新因子撞上已离库因子的旧 pnl(JOURNAL PV7)。与"离库删 snapshot"(R1)
    同构。两个池都查 —— 因子来源可能在历史上变过。"""
    removed: list[str] = []
    pnl = config.alpha_pnl / name
    if pnl.exists():
        pnl.unlink()
        removed.append(f"alpha_pnl/{name}")
    for pool in (config.pnl_automated, config.pnl_manual):
        pool_copy = pool / name
        if pool_copy.exists():
            pool_copy.unlink()
            removed.append(f"{pool.name}/{name}")
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
    info(f"    · pnl_automated|manual/{name} (bcorr 分流池副本)")
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

            # check 面产物: pnl + bcorr 池副本
            for r in _recycle_check_artifacts(name, config):
                info(f"  ✔ 已删除 {r}")

            # factor_info (级联删除 state + snapshot)
            if default_info_store(config).delete(name):
                info(f"  ✔ 已删除 factor_info (级联删除 state + snapshot)")
    except FactorLocked:
        error(f"  ✘ {name} 被另一个进程占用,稍后再试")
        return
    bottom()
