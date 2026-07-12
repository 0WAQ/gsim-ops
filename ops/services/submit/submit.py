import shutil
from pathlib import Path

from ops.core.datasource import build_npy_index
from ops.core.factor import FactorIdentity
from ops.core.factormeta import parse_factor
from ops.core.paths import META_FILENAME, FactorPaths
from ops.core.state import FactorStatus
from ops.infra.config import Config
from ops.infra.lock import FactorLocked, factor_lock
from ops.infra.repository import ArtifactScope, FactorRepository
from ops.utils.clock import now_iso
from ops.utils.func import date_range
from ops.utils.printer import banner, bottom, error, info, progress, warn

from .normalize import normalize_factor_xml


def _iter_dropbox_dirs(config: Config, users: list[str], start: str, end: str,
                       factor_name: str | None) -> list[tuple[str, Path]]:
    """Scan dropbox_path (source, read-only) and return (user, factor_dir) pairs."""
    dropbox = config.dropbox_path
    out: list[tuple[str, Path]] = []

    if factor_name is not None:
        assert start == end, "start must equal to end when --factor-name given"
        for user in users:
            d = dropbox / user / start / factor_name
            if d.exists() and d.is_dir():
                out.append((user, d))
        return out

    for user in users:
        root = dropbox / user
        if not root.exists():
            continue
        for date in date_range(start, end):
            date_path = root / date
            if not date_path.is_dir():
                continue
            for factor_dir in date_path.iterdir():
                if factor_dir.is_dir() and factor_dir.name.startswith("Alpha"):
                    out.append((user, factor_dir))
    return out


def _copy_one_to_staging(config: Config, src: Path) -> Path:
    """Copy a single factor dir into staging/AlphaXxx/ (flat).

    If staging dir already exists, it's an orphan (state has no record — see
    `ops clear`). We warn and overwrite rather than fail.
    """
    config.staging.mkdir(parents=True, exist_ok=True)
    dst = FactorPaths.of(src.name, config).staging
    if dst.exists():
        warn(f"  ⚠  {dst.name} staging 已存在(疑似 parse 失败遗留的 orphan),覆盖重写")
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return dst


def copy_to_staging(config: Config, factor_dirs: list[Path]) -> list[Path]:
    """Batch wrapper over _copy_one_to_staging. Kept as a public helper; submit
    itself interleaves copy + lock + parse per factor instead.
    """
    return [_copy_one_to_staging(config, src) for src in factor_dirs]


def submit_one(staging_dir: Path, submitted_by: str, config: Config,
               repo: FactorRepository, overwrite: bool = False,
               npy_index: dict | None = None) -> str:
    """Submit one factor from staging into state. Returns "pass" | "skip" | "fail".

    New factor (no state record)     -> repo.register(info + state 原子一个事务,
                                        version=1;原先顺序两次写,崩在中间留
                                        "有 info 无 state"半截因子)。
    Existing factor + overwrite=True -> transition state to SUBMITTED, version += 1
                                        (info 不变，只更新 state).
    Existing factor + overwrite=False -> "skip" (defensive; run_submit normally
                                        filters these out before copy).
    """
    submitted_at = now_iso()

    py_files = sorted(staging_dir.glob("*.py"))
    xml_files = sorted(staging_dir.glob("*.xml"))
    if len(py_files) != 1 or len(xml_files) != 1:
        error(f"  ✘  {staging_dir.name} 文件数不合规: "
              f".py={[p.name for p in py_files]}, .xml={[x.name for x in xml_files]} "
              f"(各需恰好 1 个,请清理多余文件后重提)")
        return "fail"

    try:
        normalize_factor_xml(staging_dir)
        meta = parse_factor(staging_dir, config,
                            submitted_by=submitted_by, submitted_at=submitted_at,
                            npy_index=npy_index)
    except SyntaxError as e:
        error(f"  ✘  {staging_dir.name} syntax error: {e}")
        return "fail"
    except Exception as e:
        error(f"  ✘  {staging_dir.name} parse failed: {e}")
        return "fail"

    if meta.discovery_method not in ("automated", "manual"):
        error(f"  ✘  {staging_dir.name} discovery_method 缺失或非法: "
              f"{meta.discovery_method!r}(须为 automated / manual,请在 "
              f"<Description discovery_method=...> 补全)")
        return "fail"

    # birthday 合法区间(L1,2026-07-12 TRIAGE:zxu birthday=20061219 错值入库)。
    # 只校验"给了但离谱"的值;缺省 0(未填)放行 —— parse 不校验,backfill
    # 存量因子无此字段(与 discovery_method 的校验分工一致)。
    if meta.birthday and not (20150101 <= meta.birthday <= 20991231):
        error(f"  ✘  {staging_dir.name} birthday 非法: {meta.birthday}"
              "(须为 20150101-20991231 区间的 yyyymmdd,"
              "请修 <Description birthday=...>)")
        return "fail"

    # Authoritative existence check under the caller's factor_lock.
    rec = repo.record(meta.name)
    if rec is not None and not overwrite:
        return "skip"

    meta_path = staging_dir / META_FILENAME
    meta.save(meta_path)

    if rec is None:
        # 新因子: info(身份)+ state(状态)经 repo.register 原子写入
        repo.register(
            FactorIdentity(
                name=meta.name,
                author=meta.author or submitted_by,
                discovery_method=meta.discovery_method,
                created_at=submitted_at,
            ),
            submitted_at=submitted_at,
            op="submit",
        )
        info(f"  ✔  {meta.name} → submitted (version=1)")
    else:
        # 已存在: 只更新 state (version += 1)，info 不变
        new_version = rec.version + 1
        repo.transition(meta.name, FactorStatus.SUBMITTED,
                        op="overwrite",
                        submitted_at=submitted_at,
                        version=new_version)
        # 覆盖提交 = 旧入库快照失效(新代码 re-check 通过后 archive 写新快照)。
        # 不删则 insert 撞 name UNIQUE 被吞,快照永远停在旧代码(full-review P0-1)。
        try:
            repo.discard_snapshot(meta.name)
        except Exception:
            warn(f"  ⚠  {meta.name} 旧 snapshot 删除失败(archive 时会自愈)")
        # 同理回收 check 面产物(pnl + bcorr 池副本):旧版本 pnl 留在池里,
        # 新代码重检时 correlation 对它 corr 通常极高 → 被迫"打败"旧的自己
        # (自鬼影,PV7)。dump/feature 服务面保留(last-known-good)。
        for r in repo.purge_artifacts(meta.name, ArtifactScope.CHECK):
            info(f"  ✔  已回收 {r}")
        info(f"  ✔  {meta.name} → submitted (version={new_version},覆盖新代码)")

    if meta.author and meta.author != submitted_by:
        warn(f"  ⚠  {meta.name}: 推断 author={meta.author!r} 与 submitter={submitted_by!r} 不一致,"
             f"后续 -u 过滤按 author,可能漏掉本因子")

    return "pass"



def run_submit(args):
    users: list[str] = [args.user]
    start: str = args.start_date
    end: str = args.end_date or start
    factor_name: str | None = args.factor_name
    overwrite: bool = args.overwrite
    config_path: Path = args.config_path

    config = Config.load(config_path)
    repo = FactorRepository(config)

    banner("因子提交")
    found = _iter_dropbox_dirs(config, users, start, end, factor_name)

    if not found:
        warn("没找到任何因子目录")
        bottom()
        return

    # 默认只提交新因子: 已入库的静默跳过 (--overwrite 才覆盖成新代码 version+1)。
    # 这里先粗过滤省掉 copy + 锁开销; submit_one 在锁内还会按 overwrite 权威判定。
    to_process: list[tuple[str, Path]] = []
    skipped = 0
    for user, src in found:
        existing = repo.record(src.name)
        if existing is not None and not overwrite:
            info(f"  ⤼  {src.name} 已入库 (status={existing.status.value}),跳过")
            skipped += 1
            continue
        to_process.append((user, src))

    if not to_process:
        warn("没有可提交的因子")
        if skipped > 0:
            warn(f"⤼ 跳过 : {skipped:>4}  (已入库,--overwrite 覆盖成新代码)")
        bottom()
        return

    # npy_index 全量 scan 一次, 整个 batch 共享, 避免 N 个因子 N 次扫盘
    npy_index = build_npy_index(config.nio_data_path)

    passed = failed = 0
    for submitted_by, src in to_process:
        name = src.name
        progress("submitting ", name)
        staged: Path | None = None
        result = "fail"
        try:
            with factor_lock(name, config):
                staged = _copy_one_to_staging(config, src)
                result = submit_one(staged, submitted_by, config, repo,
                                    overwrite=overwrite, npy_index=npy_index)
                if result != "pass":
                    # skip (并发下已入库且非 overwrite) / fail (parse / 文件数不合规):
                    # 回滚 staging,避免留下 orphan 等下次被静默覆盖
                    shutil.rmtree(staged, ignore_errors=True)
        except FactorLocked:
            warn(f"  ⚠  {name} 已被另一个进程占用,跳过")
            result = "fail"
        except Exception as e:
            # meta.save / store.put / copytree 等不可控异常: 同样回滚
            error(f"  ✘  {name} 提交异常: {e}")
            if staged is not None:
                shutil.rmtree(staged, ignore_errors=True)
            result = "fail"

        if result == "pass":
            passed += 1
        elif result == "skip":
            skipped += 1
        else:
            failed += 1

    banner("提交汇总")
    info(f"✔ 成功 : {passed:>4}")
    if failed > 0:
        error(f"✘ 失败 : {failed:>4}")
    if skipped > 0:
        warn(f"⤼ 跳过 : {skipped:>4}  (已入库,--overwrite 覆盖成新代码)")
    bottom()
