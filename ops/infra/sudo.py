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

# 写 alpha_src / staging / alpha_pnl / alpha_feature 的子命令。
# ⚠ 手抄名单是多真相源 (full-review S16),正确形态是子命令注册时声明
# writes=True 由此派生 —— G-wave 工件。在那之前,新增写命令必须记得改这里。
WRITE_COMMANDS = {
    "submit",
    "restage",
    "check",
    # run 改写 alpha_src 内 XML + gsim 写 alpha_pnl/alpha_dump,却一直缺席
    # 本名单 → JFS 下非 root 直接 EACCES (full-review 第一部分 1.2, 2026-07-07 补)
    "run",
    "rm",
    "approve",
    "cancel",
    "clear",
    "pack",
    "backfill",
}

# 这些环境变量在 sudo 提权时必须保留 (sudo 默认 strip 用户 env)。
# (OPS_STATE_REDIS_PASSWORD 随 redis state 后端退役移除, Wave 1 F2。)
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


def _get_subcommand(args) -> str | None:
    # argparse dest 用了带连字符的 "sub-command", attribute 名带连字符不可用,
    # 走 vars() 拿。
    return vars(args).get("sub-command")


def maybe_elevate(args) -> None:
    """提权检测 + exec sudo。

    满足以下全部条件才提权:
      - 当前 euid != 0
      - args 的 sub-command ∈ WRITE_COMMANDS
      - alpha_src.exists() 且 st_uid == 0 (JFS central-ops 模式)

    任一条件不满足都 no-op (legacy prod 或 read-only 命令直接走原路径)。

    提权后 os.execvp 不返回;新 sudo 进程替换当前进程。
    """
    if os.geteuid() == 0:
        return
    cmd = _get_subcommand(args)
    if cmd not in WRITE_COMMANDS:
        return
    if not _alpha_src_is_root_owned(args):
        return

    ops_bin = shutil.which("ops") or sys.argv[0]
    env_list = ",".join(_PRESERVE_ENV)
    # 只用 --preserve-env=<白名单>,不加 -E:-E 会保留整个用户环境,让精心
    # 维护的白名单形同虚设 (full-review 第一部分 sudo.py:154 项, 2026-07-07 修)。
    sudo_argv = [
        "sudo",
        f"--preserve-env={env_list}",
        ops_bin,
    ] + sys.argv[1:]
    print("  [ops] alpha_src is root-owned; elevating via sudo", file=sys.stderr)
    os.execvp("sudo", sudo_argv)  # never returns
