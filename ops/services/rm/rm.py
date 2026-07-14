"""ops rm — 彻底删除一个因子(不可逆)。

删除因子的全部落点:
  - alpha_src/<name>/           源码目录 (唯一代码副本)
  - staging/<name>/             在途副本 (restage/overwrite 召回的因子在此;不清则
    记录级联删除后必成孤儿,且 ops check 按 staging 目录扫描会自动补建记录**复活**
    刚删的因子 —— 见 JOURNAL U3)
  - alpha_pnl/<name>            回测 PNL (单文件)
  - alpha_dump/<name>/          日频目标持仓目录
  - alpha_feature/<name>.*.npy  聚合 feature
  - pnl_automated|pnl_manual/<name>  bcorr 分流池副本 (to_lib 写入;不清则已删
    因子的 pnl 永远留在对比池里参与后续因子的 bcorr)
  - factor_info PG 行           身份信息 (级联删除 state + snapshot)

没有软删/墓碑:因子被 rm 后即不存在 (要恢复只能重新 ops submit)。
默认交互确认展示完整删除清单;-y 跳过。
"""
import shutil

from ops.infra.config import Config
from ops.infra.lock import FactorLocked
from ops.infra.repository import ArtifactScope, FactorRepository
from ops.utils.printer import banner, bottom, error, highlight, info


def run_rm(args) -> None:
    name: str = args.factor_name
    config: Config = Config.load(args.config_path)
    repo = FactorRepository(config)

    # 存在性判据 = factor_info(三表之根;repo.get 的 None 语义)。问 state 会
    # 漏掉有 info 无 state 的异常孤儿 —— 那正是 rm 该能清走的东西。
    factor = repo.get(name)
    if factor is None:
        error(f"  ✘ 因子 {name} 不存在(factor_info 无记录)")
        return
    status_str = factor.status.value if factor.status else "?(无 state 记录)"

    banner(f"彻底删除因子 {name}")
    highlight(f"  状态: {status_str}(删除后即不存在,不可逆)")
    info("  将删除以下全部落点:")
    info(f"    · alpha_src/{name}/          (源码,唯一代码副本)")
    info(f"    · staging/{name}/            (在途副本,如存在)")
    info(f"    · alpha_pnl/{name}           (PNL)")
    info(f"    · alpha_dump/{name}/         (dump)")
    info(f"    · alpha_feature/{name}.*.npy (feature)")
    info(f"    · pnl_automated|manual/{name} (bcorr 分流池副本)")
    info("    · factor_info + 级联 (state + snapshot)")

    if not args.yes:
        ans = input("  确认彻底删除? 不可恢复 [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            info("  已取消")
            return

    try:
        with repo.lock(name):
            # 服务面产物: dump + feature
            for r in repo.purge_artifacts(name, ArtifactScope.SERVING):
                info(f"  ✔ 已删除 {r}")

            paths = repo.paths(name)

            # 源码目录
            if paths.src.exists():
                shutil.rmtree(paths.src)
                info(f"  ✔ 已删除 alpha_src/{name}/")

            # 在途副本:restage/overwrite 召回的因子代码在 staging。记录删除后
            # 该目录必成孤儿,且 ops check 按 staging 扫描会自动补建记录,把刚
            # 删的因子复活重新入库 —— rm 的"全落点"语义必须含它(见 JOURNAL U3)。
            if repo.unstage(name):
                info(f"  ✔ 已删除 staging/{name}/")

            # check 面产物: pnl + bcorr 池副本
            for r in repo.purge_artifacts(name, ArtifactScope.CHECK):
                info(f"  ✔ 已删除 {r}")

            # factor_info (级联删除 state + snapshot);rm 事件活过删除
            # (factor_history 无 FK)—— 因子曾经存在、谁删的,自此可追溯
            if repo.delete(name, op="rm"):
                info("  ✔ 已删除 factor_info (级联删除 state + snapshot)")
    except FactorLocked:
        error(f"  ✘ {name} 被另一个进程占用,稍后再试")
        return
    bottom()
