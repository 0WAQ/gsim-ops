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
import subprocess
import sys

from rich.console import Console

_stderr = Console(stderr=True)


# 写 alpha_src / staging / alpha_pnl / alpha_feature 的子命令
WRITE_COMMANDS = {
    "submit",
    "restage",
    "check",
    "rm",
    "approve",
    "cancel",
    "clear",
    "pack",
    "backfill",
}

# 这些环境变量在 sudo 提权时必须保留 (sudo 默认 strip 用户 env)
# 没保留 OPS_STATE_REDIS_PASSWORD 会导致 redis NOAUTH
_PRESERVE_ENV = [
    "OPS_STATE_REDIS_PASSWORD",
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


def ensure_redis_password(args) -> None:
    """新 shell 启动时 OPS_STATE_REDIS_PASSWORD 没设的兜底:
    sudo grep config.state.redis.password_file 一次拿密码塞进 env, 让后续
    self-elevate 透传到 root 子进程。

    跑了之后 sudo cache 命中, maybe_elevate 那次 exec sudo 就不再 prompt。

    - 已是 root 时 noop (Config 自己能直接读 password_file)
    - env 已有 password 时 noop
    - 没配 state.backend=redis 或没有 password_file 时 noop
    - sudo 失败 (用户取消 / 文件不存在) 静默退出, 后续步骤靠 Config / RedisStateStore
      自己处理失败 (NOAUTH 会以正常异常 surface 出来)
    """
    if os.geteuid() == 0:
        return
    env_var = "OPS_STATE_REDIS_PASSWORD"
    if os.environ.get(env_var):
        return
    config_path = getattr(args, "config_path", None)
    if config_path is None:
        return
    try:
        from ops.infra.config import Config
        config = Config.load(config_path)
    except Exception:
        return
    if getattr(config, "state_backend", "json") != "redis":
        return
    if getattr(config, "state_redis_password", None):
        return  # already populated by yaml literal
    pwd_file = getattr(config, "state_redis_password_file", None)
    pwd_key = getattr(config, "state_redis_password_key", "META_PASSWORD")
    env_name = getattr(config, "state_redis_password_env", env_var)
    if not pwd_file:
        return
    # Do NOT os.path.exists(pwd_file) here -- /etc/juicefs/ is 0700 root:root,
    # wbai can't stat its contents, so exists() returns False and we'd skip.
    # Let sudo (running as root) decide whether the file is readable.
    try:
        result = subprocess.run(
            ["sudo", "grep", "-oP", f"{pwd_key}=\\K.*", pwd_file],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception:
        return
    pwd = result.stdout.strip()
    if pwd:
        os.environ[env_name] = pwd


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
    sudo_argv = [
        "sudo",
        "-E",
        f"--preserve-env={env_list}",
        ops_bin,
    ] + sys.argv[1:]
    _stderr.print("  [dim][ops] alpha_src is root-owned; elevating via sudo[/]")
    os.execvp("sudo", sudo_argv)  # never returns
