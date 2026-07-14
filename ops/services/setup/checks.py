"""setup 项目注册表 —— 本机 alphalib 部署的应然形态(SSOT)。

每项一个 `SetupCheck`(check 只读判定,fix 幂等补建或 None)。新增项 = 表里
加一行,与 check 流水线的 PIPELINE 同款模式。**应然形态与部署变更同批改**
(见 STAGING_IS_SHARED)。

范围:存储布局 + 权限模型 + 环境可达性 + PG(像 uv 管 python 项目一样管
alphalib 部署)。JFS 挂载本身不管(归 scripts/juicefs-poc/join.sh);
数据对账(盘 ↔ PG)留给 ops doctor。

fix 原则:**只补建缺失(mkdir / symlink / groupadd),绝不改动已存在的东西**
—— 存在但形态错误(软链指错 / gid 被占)只报告并给手工指引,防补建变破坏。
唯一例外是顶层权限(chown/chmod,照抄 scripts/juicefs-poc/02-layout.sh 模型,
只动顶层目录不递归)。
"""
from __future__ import annotations

import grp
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ops.infra.config import Config

# 共享 staging(见 docs/design/shared-staging-queue.md):staging 的应然 = JFS
# 实目录(全局共享,多机 submit / 170 集中 check 的数据面)。物理切换步骤见
# DEPLOY-SHARED-STAGING.md。
STAGING_IS_SHARED = True

OK, FAIL, WARN, SKIP = "ok", "fail", "warn", "skip"

# 权限组(02-layout.sh 的既定模型):组名 → gid。仅作跨机只读 label,写走 root。
GROUPS = {"alpha-core": 59000, "alpha-data": 59001}


@dataclass
class CheckResult:
    check_id: str
    title: str
    status: str                 # ok | fail | warn | skip
    detail: str = ""
    fixable: bool = False
    fixed: bool = False


@dataclass(frozen=True)
class SetupCheck:
    check_id: str
    title: str
    severity: str               # 不达标记 FAIL(任何节点必须绿)/ WARN(角色相关)
    check: Callable             # (ctx) -> (bool, detail)
    fix: Callable | None = None # (ctx) -> None;幂等,只补建缺失


@dataclass
class Ctx:
    """检查上下文。mounts / legacy_link 可注入(测试不碰 /proc、/mnt)。"""
    config: "Config"
    mounts: str = ""
    legacy_link: Path = Path("/mnt/storage/alphalib")

    @property
    def root(self) -> Path:
        """alphalib 根(挂载点)。"""
        return self.config.alpha_src.parent

    @property
    def sidecar(self) -> Path:
        """本机 sidecar:<root>.local(部署约定)。"""
        return self.root.with_name(self.root.name + ".local")


def _read_mounts() -> str:
    try:
        return Path("/proc/mounts").read_text()
    except OSError:
        return ""


# ------------------------------------------------------------------ hosts 声明

def _check_host_declared(ctx: Ctx) -> tuple[bool, str]:
    declared = getattr(ctx.config, "host_declared", None)
    hostname = getattr(ctx.config, "hostname", "") or "<unknown>"
    if declared is None:
        return True, "skip: config 无 hosts 块(单机/dev 配置)"
    if declared:
        return True, f"hosts.{hostname} 命中,路径按声明解析"
    return False, (f"本机 hostname '{hostname}' 未在 config hosts 块声明"
                   "(正在用 vars 基础值;请在 config.yaml hosts 加一行)")


# ------------------------------------------------------------------ 存储布局

def _check_mount(ctx: Ctx) -> tuple[bool, str]:
    from .jfs import actual_jfs_mount
    mounts = ctx.mounts or _read_mounts()
    found = actual_jfs_mount(mounts)
    if found is not None and found[0] == ctx.root:
        return True, f"{ctx.root} (fuse.juicefs)"
    if found is not None:
        # 声明与实挂不一致 —— 部署变更场景(如 170 /ext4→/nvme125)
        return False, (f"JFS 卷 '{found[1]}' 挂在 {found[0]},声明是 {ctx.root};"
                       "跑 `ops setup --migrate-mount` 迁移")
    return False, "本机无 JuiceFS 挂载(首次接入先跑 scripts/juicefs-poc/join.sh)"


def _shared_dirs(ctx: Ctx) -> list[Path]:
    c = ctx.config
    return [c.alpha_src, c.alpha_pnl, c.alpha_feature]


def _pool_dirs(ctx: Ctx) -> list[Path]:
    return [ctx.config.pnl_automated, ctx.config.pnl_manual]


def _check_real_dirs(paths: list[Path]) -> tuple[bool, str]:
    bad = []
    for p in paths:
        if not p.is_dir():
            bad.append(f"{p.name}: 缺失")
        elif p.is_symlink():
            bad.append(f"{p.name}: 是软链(应为 JFS 实目录)")
    return (not bad), "; ".join(bad) or "全部为实目录"


def _fix_mkdirs(paths: list[Path]) -> None:
    for p in paths:
        if not p.exists():
            p.mkdir(parents=True)


def _check_shared(ctx: Ctx) -> tuple[bool, str]:
    return _check_real_dirs(_shared_dirs(ctx))


def _fix_shared(ctx: Ctx) -> None:
    _fix_mkdirs(_shared_dirs(ctx))


def _check_pools(ctx: Ctx) -> tuple[bool, str]:
    return _check_real_dirs(_pool_dirs(ctx))


def _fix_pools(ctx: Ctx) -> None:
    _fix_mkdirs(_pool_dirs(ctx))


def _check_sidecar_link(ctx: Ctx, mount_path: Path) -> tuple[bool, str]:
    """dump/staging 的应然:挂载点下软链 → <root>.local/<name>(相对 target;
    软链是 JFS 内单一对象,各机按本机挂载点解析)。存在但指错:只报告。"""
    want = ctx.sidecar / mount_path.name
    if not mount_path.is_symlink():
        if mount_path.exists():
            return False, (f"{mount_path.name} 存在但不是软链"
                           f"(应指 {want};手工核实,setup 不动已存在物)")
        return False, f"{mount_path.name} 软链缺失(应指 {want})"
    if mount_path.resolve() != want.resolve():
        return False, f"{mount_path.name} 软链指向 {mount_path.resolve()},应为 {want}(手工核实)"
    if not want.is_dir():
        return False, f"软链正确但 sidecar 目录 {want} 缺失"
    return True, f"{mount_path.name} → {want}"


def _fix_sidecar_link(ctx: Ctx, mount_path: Path) -> None:
    want = ctx.sidecar / mount_path.name
    want.mkdir(parents=True, exist_ok=True)
    if not mount_path.exists() and not mount_path.is_symlink():
        # 相对 target(../<root>.local/<name>):与部署约定一致
        mount_path.symlink_to(Path("..") / ctx.sidecar.name / mount_path.name)


def _check_staging(ctx: Ctx) -> tuple[bool, str]:
    if STAGING_IS_SHARED:
        return _check_real_dirs([ctx.config.staging])
    return _check_sidecar_link(ctx, ctx.config.staging)


def _fix_staging(ctx: Ctx) -> None:
    if STAGING_IS_SHARED:
        _fix_mkdirs([ctx.config.staging])
    else:
        _fix_sidecar_link(ctx, ctx.config.staging)


def _check_dump(ctx: Ctx) -> tuple[bool, str]:
    return _check_sidecar_link(ctx, ctx.config.alpha_dump)


def _fix_dump(ctx: Ctx) -> None:
    _fix_sidecar_link(ctx, ctx.config.alpha_dump)


def _check_legacy_link(ctx: Ctx) -> tuple[bool, str]:
    legacy = ctx.legacy_link
    if not legacy.is_symlink():
        return False, f"{legacy} 软链缺失(老脚本/固定路径文档经它访问本机 alphalib)"
    if legacy.resolve() != ctx.root.resolve():
        return False, f"{legacy} 指向 {legacy.resolve()},应为 {ctx.root}(手工核实)"
    return True, f"{legacy} → {ctx.root}"


def _fix_legacy_link(ctx: Ctx) -> None:
    legacy = ctx.legacy_link
    if not legacy.exists() and not legacy.is_symlink():
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.symlink_to(ctx.root)


# ------------------------------------------------------------------ 权限模型

def _check_groups(ctx: Ctx) -> tuple[bool, str]:
    bad = []
    for name, gid in GROUPS.items():
        try:
            g = grp.getgrnam(name)
        except KeyError:
            bad.append(f"{name}(gid {gid})缺失")
            continue
        if g.gr_gid != gid:
            bad.append(f"{name} gid={g.gr_gid} != {gid}(手工核实)")
    return (not bad), "; ".join(bad) or "alpha-core/alpha-data 在位"


def _fix_groups(ctx: Ctx) -> None:
    for name, gid in GROUPS.items():
        try:
            grp.getgrnam(name)
            continue                      # 已存在(gid 对错由 check 报告)
        except KeyError:
            pass
        try:
            grp.getgrgid(gid)
            continue                      # gid 被占:不动,check 报告
        except KeyError:
            pass
        subprocess.run(["groupadd", "-g", str(gid), name], check=True)


def _perm_table(ctx: Ctx) -> dict[Path, tuple[str, int]]:
    """顶层目录 → (组, mode)。照抄 02-layout.sh;只列顶层,不递归。"""
    c = ctx.config
    table: dict[Path, tuple[str, int]] = {
        ctx.root: ("alpha-data", 0o2755),
        c.alpha_src: ("alpha-core", 0o2750),
        c.alpha_pnl: ("alpha-data", 0o2755),
        c.alpha_feature: ("alpha-data", 0o2755),
        c.pnl_automated: ("alpha-data", 0o2755),
        c.pnl_manual: ("alpha-data", 0o2755),
        ctx.sidecar: ("alpha-data", 0o2755),
    }
    if STAGING_IS_SHARED:
        table[c.staging] = ("alpha-core", 0o2750)
    else:
        table[ctx.sidecar / "staging"] = ("alpha-core", 0o2750)
    table[ctx.sidecar / "alpha_dump"] = ("alpha-data", 0o2755)
    return table


def _check_perms(ctx: Ctx) -> tuple[bool, str]:
    bad = []
    for p, (group, mode) in _perm_table(ctx).items():
        if not p.is_dir():
            continue                      # 缺失归布局项报,此处只看在位者
        st = p.stat()
        try:
            want_gid = grp.getgrnam(group).gr_gid
        except KeyError:
            bad.append(f"{p.name}: 组 {group} 不存在")
            continue
        if st.st_uid != 0 or st.st_gid != want_gid or (st.st_mode & 0o7777) != mode:
            bad.append(f"{p.name}: {st.st_uid}:{st.st_gid} {oct(st.st_mode & 0o7777)}"
                       f" != root:{group} {oct(mode)}")
    return (not bad), "; ".join(bad) or "顶层 owner/组/setgid 符合模型"


def _fix_perms(ctx: Ctx) -> None:
    import os
    for p, (group, mode) in _perm_table(ctx).items():
        if not p.is_dir():
            continue
        want_gid = grp.getgrnam(group).gr_gid   # 组缺失则抛,engine 记 fix 失败
        os.chown(p, 0, want_gid)
        os.chmod(p, mode)


# ------------------------------------------------------------------ 环境 / 后端

def _check_paths_exist(paths: dict[str, Path]) -> tuple[bool, str]:
    missing = [f"{k}: {v}" for k, v in paths.items() if not v.exists()]
    return (not missing), "; ".join(missing) or "在位"


def _check_nio(ctx: Ctx) -> tuple[bool, str]:
    return _check_paths_exist({"nio_data_path": ctx.config.nio_data_path})


def _check_dropbox(ctx: Ctx) -> tuple[bool, str]:
    return _check_paths_exist({"dropbox_path": ctx.config.dropbox_path})


def _check_gsim(ctx: Ctx) -> tuple[bool, str]:
    c = ctx.config
    return _check_paths_exist({
        "run_script": c.run_script,
        "simsummary_script": c.simsummary_script,
        "bcorr_script": c.bcorr_script,
        "pnl_prod_path": c.pnl_prod_path,
    })


def _pg_conninfo(ctx: Ctx) -> tuple[str | None, str]:
    """(conninfo, skip/fail 原因)。诊断探测统一 5s 有界直连,不走 get_pool
    (进程级池注册表不该被探测污染,且池的重试会让不可达时挂起半分钟以上)。"""
    backend = (getattr(ctx.config, "state_backend", None) or "").lower()
    if backend != "postgres":
        return None, f"skip: state.backend={backend or '未配置'}(非 PG 环境)"
    conninfo = getattr(ctx.config, "state_postgres_conninfo", None)
    if not conninfo:
        return None, "state.postgres 配置不完整(conninfo 解析失败;密码文件在位?)"
    return conninfo, ""


def _check_pg(ctx: Ctx) -> tuple[bool, str]:
    conninfo, reason = _pg_conninfo(ctx)
    if conninfo is None:
        return reason.startswith("skip:"), reason
    try:
        from ops.infra.pg import probe
        probe(conninfo, statements=tuple(
            f"SELECT 1 FROM {t} LIMIT 1"  # noqa: S608 — 表名白名单
            for t in ("factor_info", "factor_state", "factor_snapshot")))
        return True, "连接 + 三表在位"
    except Exception as e:  # 诊断命令:任何失败都要变成报告而不是崩溃
        return False, f"PG 不可达或三表缺失: {e}"


def _check_lock(ctx: Ctx) -> tuple[bool, str]:
    conninfo, reason = _pg_conninfo(ctx)
    if conninfo is None:
        return reason.startswith("skip:"), (reason if reason.startswith("skip:")
                                            else f"前置不满足:{reason}")
    try:
        from ops.infra.pg import probe
        probe(conninfo)                # 有界预探测:PG 不可达时不进 factor_lock 长等
    except Exception as e:
        return False, f"PG 不可达,锁不可用: {e}"
    try:
        from ops.infra.lock import factor_lock
        with factor_lock("__setup_probe__", ctx.config):
            pass
        return True, "跨机 advisory lock 往返正常"
    except Exception as e:
        return False, f"锁往返失败: {e}"


# ------------------------------------------------------------------ 注册表

CHECKS: tuple[SetupCheck, ...] = (
    SetupCheck("host-declared", "hosts 声明命中", WARN, _check_host_declared),
    SetupCheck("mount", "alphalib JFS 挂载", FAIL, _check_mount),
    SetupCheck("shared-dirs", "共享目录 (src/pnl/feature)", FAIL, _check_shared, _fix_shared),
    SetupCheck("pool-dirs", "bcorr 分流池 (automated/manual)", FAIL, _check_pools, _fix_pools),
    SetupCheck("staging", "staging 形态", FAIL, _check_staging, _fix_staging),
    SetupCheck("dump", "alpha_dump 软链 → sidecar", FAIL, _check_dump, _fix_dump),
    SetupCheck("legacy-link", "/mnt/storage/alphalib 兼容软链", WARN, _check_legacy_link, _fix_legacy_link),
    SetupCheck("groups", "权限组 alpha-core/alpha-data", WARN, _check_groups, _fix_groups),
    SetupCheck("perms", "顶层目录权限模型", WARN, _check_perms, _fix_perms),
    SetupCheck("pg", "Postgres 连接 + 三表", FAIL, _check_pg),
    SetupCheck("lock", "跨机因子锁往返", FAIL, _check_lock),
    SetupCheck("nio-data", "nio_data_path 数据", WARN, _check_nio),
    SetupCheck("dropbox", "dropbox 投递目录(submit 节点需要)", WARN, _check_dropbox),
    SetupCheck("gsim", "gsim 工具链(check 节点需要)", WARN, _check_gsim),
)
