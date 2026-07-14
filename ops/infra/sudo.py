"""ops 进程提权 wrapper.

JFS 集中运维模型下 alpha_src / staging / alpha_pnl / alpha_feature
都是 root-owned, wbai 用户跑写命令 (submit/check/rm/pack...) 直接 shutil.move
会 EACCES。

策略: 进程入口检测 alpha_src 是否 root-owned + 当前是否 root, 不满足
条件就 os.execvp('sudo')替换自身, 整个 ops 进程跑成 root.

为什么不用 per-call sudo wrapper:
- 写路径分散在 6+ 个 service 里, 每个调用点用 subprocess.run(['sudo', ...])
  拼接会有 quoting 风险, 代码污染严重
- 进程级提权一次 sudo prompt (或 NOPASSWD), 后续零开销
- 在 prod legacy 模式 (alpha_src wbai-owned) 自动 noop, 不影响现有部署

部署侧:
- 推荐配 /etc/sudoers.d/wbai-ops 让 wbai 跑 /home/wbai/.local/bin/ops 时
  NOPASSWD, 否则每次 write 命令都会 prompt
"""
import os
import shutil
import sys

# 写命令集是声明派生,不手抄:每个写子命令在注册处 `mark_write(parser)`
# 声明(ops/cli/common.py),落在 args.is_write_command 上,本模块只消费。
# 单一定义 —— 新增写命令漏声明会在 JFS 环境首次写时 EACCES 响亮暴露,而非
# 静默绕过提权。别退回手抄集合(多真相源,漏声明即静默绕过提权)。

# 这些环境变量在 sudo 提权时必须保留 (sudo 默认 strip 用户 env)。
_PRESERVE_ENV = [
    "OPS_CONFIG",
    # OPS_* prefix vars consumed by Config._resolve_vars
    "OPS_GSIM_HOME",
    "OPS_STORAGE",
    "OPS_WORKSPACE",
    "OPS_ALPHALIB_ROOT",
]


def _alpha_src_is_root_owned(args) -> bool:
    """args.config_path -> Config -> alpha_src.stat().st_uid == 0?"""
    config_path = getattr(args, "config_path", None)
    if config_path is None:
        return False
    try:
        from ops.infra.config import Config
        config = Config.load(config_path)
        if not config.alpha_src.exists():
            return False
        return config.alpha_src.stat().st_uid == 0
    except Exception:
        return False


def maybe_elevate(args) -> None:
    """提权检测 + exec sudo。

    满足以下全部条件才提权:
      - 当前 euid != 0
      - 子命令注册时声明了写性(cli/common.mark_write → args.is_write_command)
      - alpha_src.exists() 且 st_uid == 0 (JFS central-ops 模式)

    任一条件不满足都 no-op (legacy prod 或 read-only 命令直接走原路径)。

    提权后 os.execvp 不返回;新 sudo 进程替换当前进程。
    """
    if os.geteuid() == 0:
        return
    if not getattr(args, "is_write_command", False):
        return
    if not _alpha_src_is_root_owned(args):
        return

    ops_bin = shutil.which("ops") or sys.argv[0]
    env_list = ",".join(_PRESERVE_ENV)
    # 只用 --preserve-env=<白名单>,不加 -E:-E 会保留整个用户环境,让白名单
    # 形同虚设。
    sudo_argv = [
        "sudo",
        f"--preserve-env={env_list}",
        ops_bin,
    ] + sys.argv[1:]
    print("  [ops] alpha_src is root-owned; elevating via sudo", file=sys.stderr)
    os.execvp("sudo", sudo_argv)  # never returns
