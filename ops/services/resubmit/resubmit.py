"""ops resubmit — 已有因子提交新代码(从 dropbox 覆盖,version += 1)。

研究员修改了因子代码后,从 dropbox 重新提交。因子名必须已存在于 state 中,
否则拒绝(提示用 ops submit)。

流程:
1. 扫描 dropbox(复用 submit 的扫描逻辑)
2. 过滤:只保留 state 中已存在的因子
3. 复制到 staging,normalize XML,生成 meta.json
4. store.transition → SUBMITTED,version += 1
5. 旧 alpha_src 中的代码保留(作为对比基准)
6. dump / feature / pnl 保留

批量模式采用 apt-install 风格交互;-y 跳过确认。
"""
import shutil
from datetime import datetime
from pathlib import Path

from ops.infra.config import Config
from ops.infra.lock import factor_lock, FactorLocked
from ops.infra.store import default_store, StateStore
from ops.core.state import FactorRecord, FactorStatus
from ops.services.submit.submit import _iter_dropbox_dirs, copy_to_staging
from ops.services.submit.parser import parse_factor
from ops.services.submit.normalize import normalize_factor_xml
from ops.utils.logger.log import banner, bottom, info, warn, error, highlight


META_FILENAME = "meta.json"


def _resubmit_one(staging_dir: Path, submitted_by: str, config: Config,
                  store: StateStore) -> bool:
    """Resubmit a single factor: normalize, parse, bump version, transition state."""
    name = staging_dir.name
    submitted_at = datetime.now().isoformat(timespec="seconds")

    py_files = sorted(staging_dir.glob("*.py"))
    xml_files = sorted(staging_dir.glob("*.xml"))
    if len(py_files) != 1 or len(xml_files) != 1:
        error(f"  ✘  {name} 文件数不合规: "
              f".py={[p.name for p in py_files]}, .xml={[x.name for x in xml_files]} "
              f"(各需恰好 1 个)")
        return False

    try:
        normalize_factor_xml(staging_dir)
        meta = parse_factor(staging_dir, config,
                            submitted_by=submitted_by, submitted_at=submitted_at)
    except SyntaxError as e:
        error(f"  ✘  {name} syntax error: {e}")
        return False
    except Exception as e:
        error(f"  ✘  {name} parse failed: {e}")
        return False

    meta_path = staging_dir / META_FILENAME
    meta.save(meta_path)

    rec = store.get(name)
    new_version = (rec.version if rec else 0) + 1
    store.transition(name, FactorStatus.SUBMITTED,
                     submitted_at=submitted_at,
                     submitted_by=submitted_by,
                     version=new_version)

    info(f"  ✔  {name} → submitted (version={new_version})")
    return True


def run_resubmit(args) -> None:
    users: list[str] = [args.user]
    start: str = args.start_date
    end: str = args.end_date or start
    factor_name: str | None = args.factor_name
    config_path: Path = args.config_path

    config = Config.load(config_path)
    store = default_store(config)

    banner("因子重提交")
    found = _iter_dropbox_dirs(config, users, start, end, factor_name)

    if not found:
        warn("  没找到任何因子目录")
        bottom()
        return

    # 过滤: 只保留 state 中已存在的因子
    existing = []
    new_factors = []
    for user, d in found:
        rec = store.get(d.name)
        if rec is not None:
            existing.append((user, d))
        else:
            new_factors.append(d.name)

    if new_factors:
        for name in new_factors:
            warn(f"  ⚠  {name} 不在 state 中,请用 ops submit 提交新因子")

    if not existing:
        error("  ✘ 没有可 resubmit 的因子(均为新因子,请用 ops submit)")
        bottom()
        return

    # 显示计划
    highlight(f"  将 resubmit {len(existing)} 个因子(新代码覆盖,version += 1):")
    for user, d in existing:
        rec = store.get(d.name)
        ver = rec.version if rec else "?"
        info(f"    · {d.name:<40}  v{ver} → v{int(ver)+1 if isinstance(ver, int) else '?'}  ← {d}")

    if not args.yes:
        ans = input(f"  确认 resubmit {len(existing)} 个因子? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            info("  已取消")
            bottom()
            return

    # 复制到 staging
    src_dirs = [d for _, d in existing]
    staging_dirs = copy_to_staging(config, src_dirs)

    passed = failed = 0
    for (user, src), staged in zip(existing, staging_dirs):
        print("resubmitting ", end=""); highlight(f"{staged.name}")
        try:
            with factor_lock(staged.name):
                ok = _resubmit_one(staged, user, config, store)
        except FactorLocked:
            warn(f"  ⚠  {staged.name} 已被另一个进程占用,跳过")
            ok = False
        if ok:
            passed += 1
        else:
            failed += 1

    banner("重提交汇总")
    info(f"✔ 成功 : {passed:>4}")
    if failed > 0:
        error(f"✘ 失败 : {failed:>4}")
    bottom()
