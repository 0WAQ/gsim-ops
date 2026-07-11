"""JFS 客户端挂载点迁移编排(`ops setup --migrate-mount`)。

场景:已 join 的客户端要换挂载点(声明 B ≠ 实挂 A;170 /ext4 → /nvme125,
2026-07-11)。迁移 = 改配置重挂,不搬卷内数据 —— staging/dump 共享软链是
相对 target,自动适配新挂载点。

实现输入来自 DISCOVER-170-ENV-RESULT(2026-07-11 实测):
- `/etc/systemd/system/juicefs-<name>.service` 的 ExecStart **硬编码**挂载点/
  cache/meta(EnvironmentFile 变量未被引用)→ 迁移必须重渲染 unit;
- `/etc/juicefs-poc.env` 六键是路径声明(JFS_MOUNT/CACHE_DIR/LOCAL_DIR/
  META_URL/REDIS_LOCAL/CACHE_SIZE_MB),未知键原样保留。

**unit 模板正主自此在本模块**(`render_unit`,golden test 用 170 现役 unit
原文钉住;scripts/juicefs-poc/04-systemd.sh 保留 bootstrap 用途,头注指向此处)。

红线:只动本机(env / unit / sidecar / 兼容软链),不碰 JFS 卷内数据、
metadata、MinIO;旧址(旧挂载点目录、旧 cache、空 sidecar)**报告不删**;
任一步失败恢复备份并重启旧配置。

全部系统交互可注入(run / 路径 / mounts / stats),控制流测试无需 root。
"""
from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ops.infra.config import Config

ENV_PATH = Path("/etc/juicefs-poc.env")
UNIT_DIR = Path("/etc/systemd/system")
LEGACY_LINK = Path("/mnt/storage/alphalib")
BAK_SUFFIX = ".ops-migrate-bak"


class MigrateError(RuntimeError):
    """迁移前置不满足 / 执行失败(信息里带已执行步骤与回滚状态)。"""


# ------------------------------------------------------------------ env 文件

def parse_env(text: str) -> dict[str, str]:
    """裸 KEY=VALUE 解析(join.sh 写的格式);注释/空行忽略。"""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def render_env(values: dict[str, str], original: str) -> str:
    """在原文本上按键替换(保注释/顺序);缺失键追加到尾部。"""
    lines = original.splitlines()
    seen: set[str] = set()
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k = s.split("=", 1)[0].strip()
        if k in values:
            lines[i] = f"{k}={values[k]}"
            seen.add(k)
    for k, v in values.items():
        if k not in seen:
            lines.append(f"{k}={v}")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ unit 模板

def render_unit(name: str, mount: Path, cache_dir: Path,
                cache_size_mb: str, meta_url: str) -> str:
    """systemd unit 渲染 —— 结构与生产 unit 逐段一致(golden test 钉住)。"""
    return f"""[Unit]
Description=JuiceFS mount {mount}
After=network-online.target
Wants=network-online.target


[Service]
Type=forking
EnvironmentFile=/etc/juicefs/{name}.env
ExecStartPre=/bin/mkdir -p {mount}
ExecStart=/usr/local/bin/juicefs mount \\
  --cache-dir={cache_dir} \\
  --cache-size={cache_size_mb} \\
  --writeback \\
  --background \\
  {meta_url} {mount}
# 三级 fallback: 标准 umount → fusermount lazy → umount -l
# 防有进程持有 mount 时卡 deactivating。前两步失败也继续 (- 前缀 + bash || 链)。
ExecStop=/bin/bash -c '/usr/local/bin/juicefs umount {mount} 2>/dev/null || /bin/fusermount -uz {mount} 2>/dev/null || /bin/umount -l {mount} 2>/dev/null || true'
Restart=on-failure
RestartSec=5
TimeoutStartSec=60
TimeoutStopSec=60

[Install]
WantedBy=multi-user.target
"""


# ------------------------------------------------------------------ 探测

def actual_jfs_mount(mounts_text: str) -> tuple[Path, str] | None:
    """/proc/mounts 里第一个 JuiceFS 挂载 → (挂载点, 卷名);没有 → None。"""
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and "juicefs" in parts[2] and parts[0].startswith("JuiceFS:"):
            return Path(parts[1]), parts[0].split(":", 1)[1]
    return None


def writeback_drained(stats_text: str) -> bool:
    """<mount>/.stats 的 juicefs_staging_blocks == 0。"""
    for line in stats_text.splitlines():
        if line.startswith("juicefs_staging_blocks"):
            return line.split()[-1] == "0"
    return False  # 读不到指标按未排干处理(保守)


# ------------------------------------------------------------------ 编排

@dataclass
class MigrateIO:
    """系统交互面(测试注入替身)。"""
    run: Callable[..., "subprocess.CompletedProcess"] = field(
        default=lambda *a, **kw: subprocess.run(*a, **kw))  # noqa: S603
    env_path: Path = ENV_PATH
    unit_dir: Path = UNIT_DIR
    legacy_link: Path = LEGACY_LINK
    read_mounts: Callable[[], str] = lambda: Path("/proc/mounts").read_text()
    sleep: Callable[[float], None] = time.sleep


def _systemctl(io: MigrateIO, *args: str) -> None:
    r = io.run(["systemctl", *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise MigrateError(f"systemctl {' '.join(args)} 失败: {r.stderr or r.stdout}")


def migrate_mount(config: "Config", io: MigrateIO | None = None) -> list[str]:
    """把本机 JFS 挂载点迁到声明位置。返回步骤日志;失败抛 MigrateError
    (抛前尽力恢复备份并重启旧配置)。"""
    io = io or MigrateIO()
    log: list[str] = []
    target = config.alpha_src.parent                       # 声明 B

    # ---- 前置守卫(全部只读,违反即拒,零改动) ----
    found = actual_jfs_mount(io.read_mounts())
    if found is None:
        raise MigrateError("本机没有 JuiceFS 挂载 —— 首次接入请用 scripts/juicefs-poc/join.sh")
    current, name = found                                  # 实挂 A + 卷名
    if current == target:
        log.append(f"挂载点已在声明位置 {target},无需迁移")
        return log
    if not target.parent.is_dir():
        raise MigrateError(f"目标父目录 {target.parent} 不存在(目标盘未就绪)")
    if not io.env_path.is_file():
        raise MigrateError(f"{io.env_path} 缺失(非 join.sh 部署的客户端?)")
    unit_path = io.unit_dir / f"juicefs-{name}.service"
    if not unit_path.is_file():
        raise MigrateError(f"{unit_path} 缺失")
    stats = current / ".stats"
    try:
        drained = writeback_drained(stats.read_text())
    except OSError as e:
        raise MigrateError(f"读 {stats} 失败: {e}") from e
    if not drained:
        raise MigrateError("writeback 未排干(juicefs_staging_blocks != 0),稍后重试")

    env_text = io.env_path.read_text()
    env = parse_env(env_text)
    old_cache = Path(env.get("JFS_CACHE_DIR", str(current.parent / "jfs-cache")))
    old_local = Path(env.get("JFS_LOCAL_DIR", f"{current}.local"))
    new_cache = target.parent / old_cache.name             # 同盘、沿用旧名
    new_local = Path(f"{target}.local")
    log.append(f"迁移计划: {current} → {target}(卷 {name};cache → {new_cache};"
               f"sidecar → {new_local})")

    # ---- 备份 ----
    env_bak = io.env_path.with_name(io.env_path.name + BAK_SUFFIX)
    unit_bak = unit_path.with_name(unit_path.name + BAK_SUFFIX)
    shutil.copy2(io.env_path, env_bak)
    shutil.copy2(unit_path, unit_bak)
    log.append(f"备份: {env_bak.name} / {unit_bak.name}")

    def rollback(reason: str) -> None:
        shutil.copy2(env_bak, io.env_path)
        shutil.copy2(unit_bak, unit_path)
        try:
            _systemctl(io, "daemon-reload")
            _systemctl(io, "start", f"juicefs-{name}")
        except MigrateError as e:
            raise MigrateError(
                f"{reason};且回滚重启失败({e})—— 人工介入:备份已恢复,"
                f"手工 systemctl start juicefs-{name}") from None
        raise MigrateError(f"{reason} —— 已回滚到旧配置并重启,盘面未变")

    # ---- 执行 ----
    _systemctl(io, "stop", f"juicefs-{name}")
    log.append(f"已停 juicefs-{name}(三级 umount fallback 由 unit ExecStop 负责)")

    env.update({"JFS_MOUNT": str(target), "JFS_CACHE_DIR": str(new_cache),
                "JFS_LOCAL_DIR": str(new_local)})
    io.env_path.write_text(render_env(env, env_text))
    unit_path.write_text(render_unit(
        name, target, new_cache,
        env.get("JFS_CACHE_SIZE_MB", "102400"), env["JFS_META_URL"]))
    _systemctl(io, "daemon-reload")
    log.append(f"已改写 {io.env_path} + {unit_path.name}")

    target.mkdir(parents=True, exist_ok=True)
    new_local.mkdir(parents=True, exist_ok=True)
    if old_local.is_dir():
        moved = 0
        for entry in old_local.iterdir():
            dest = new_local / entry.name
            if not dest.exists():
                shutil.move(str(entry), str(dest))
                moved += 1
        log.append(f"sidecar 存量搬运 {old_local} → {new_local}({moved} 项)")

    try:
        _systemctl(io, "start", f"juicefs-{name}")
    except MigrateError as e:
        rollback(f"新配置启动失败: {e}")
    for _ in range(30):                                    # 最多等 ~30s
        entry = actual_jfs_mount(io.read_mounts())
        if entry and entry[0] == target:
            break
        io.sleep(1)
    else:
        rollback(f"启动后 {target} 未出现在 /proc/mounts")
    try:
        mounted_ok = any((target / "alpha_src").iterdir())
    except OSError:
        mounted_ok = False
    if not mounted_ok:
        rollback(f"{target}/alpha_src 缺失或为空 —— 挂载内容异常")
    log.append(f"已挂载 {target}(fuse.juicefs)且共享数据可见")

    # 兼容软链重指(migrate 语义允许改已存在软链;缺省 setup 只建缺失)
    if io.legacy_link.is_symlink() or not io.legacy_link.exists():
        io.legacy_link.parent.mkdir(parents=True, exist_ok=True)
        tmp = io.legacy_link.with_name(io.legacy_link.name + ".ops-tmp")
        tmp.symlink_to(target)
        tmp.replace(io.legacy_link)                        # 原子重指
        log.append(f"{io.legacy_link} → {target}")
    else:
        log.append(f"⚠ {io.legacy_link} 存在且非软链,未动(手工核实)")

    log.append(f"旧址保留待人工清理: {current}(空挂载点目录)、{old_cache}(旧 cache)、"
               f"{old_local}(搬空的 sidecar);备份 {env_bak.name}/{unit_bak.name} 验证后可删")
    return log
