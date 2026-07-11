import argparse
import sys
from pathlib import Path

from rich import box
from rich.console import Console
from rich.table import Table

from ops.cli.common import add_config_arg, load_config, mark_write
from ops.services.setup import has_failures, run_setup

_STYLE = {"ok": "green", "fail": "red", "warn": "yellow", "skip": "dim"}
_MARK = {"ok": "✔", "fail": "✘", "warn": "⚠", "skip": "-"}


class _CheckAction(argparse.Action):
    """--check 只读:同时撤销写声明(sudo self-elevate 据 is_write_command
    提权 —— 不该为看一眼清单就 sudo)。缺省 setup 是补建(创建型写)。"""

    def __init__(self, option_strings, dest, **kw):
        super().__init__(option_strings, dest, nargs=0, default=False, **kw)

    def __call__(self, parser, namespace, values, option_string=None):
        setattr(namespace, self.dest, True)
        namespace.is_write_command = False


def add_setup_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "setup",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="拉平本机 alphalib 部署(幂等补建;--check 只读体检)",
        epilog="""\
Example:
    ops setup                  # 按声明拉平本机:补建缺失的目录/软链/权限组(幂等)
    ops setup --check          # 只看不动:✔/✘/⚠ 清单 + 退出码(验收/巡检)
    ops setup --migrate-mount  # 声明变更收敛:JFS 挂载点迁到 hosts 声明位置

像 uv sync 之于 python 项目:部署声明在 config.yaml(hosts 块按 hostname
匹配挂载点),一条命令让本机就绪,之后 ops 开箱即用。
FAIL = 存储部署错误(任何节点必须绿);WARN = 角色相关(worker 无 dropbox、
纯投递机无 gsim 属正常)。退出码:有 FAIL → 1。
补建铁律:缺省 setup 只创建缺失,绝不改动已存在的东西(顶层权限对齐除外,
照 scripts/juicefs-poc/02-layout.sh 模型);**声明变更的收敛动作全部藏在显式
flag 后**(--migrate-mount:重渲染 unit + 重挂 + 搬 sidecar,失败自动回滚,
旧址报告不删)。JFS 首次接入仍归 join.sh。
项目注册表:ops/services/setup/checks.py(新增检查 = 加一行)。
""",
    )
    parser.add_argument("--check", action=_CheckAction,
                        help="只读体检,不做任何补建")
    parser.add_argument("--migrate-mount", action="store_true",
                        help="把本机 JFS 挂载点迁到 hosts 声明位置"
                             "(显式变更操作:重渲染 unit + 重挂 + 搬 sidecar)")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="跳过 --migrate-mount 的交互确认")
    add_config_arg(parser)
    mark_write(parser)              # 缺省补建是写;--check 经 _CheckAction 撤销
    parser.set_defaults(func=run_setup_cli)


def run_setup_cli(args):
    # 展示层在 cli(services/setup 零 rich)—— 分层示范件,勿回搬 services。
    config = load_config(args.config_path)

    console = Console()
    if args.migrate_mount:
        if args.check:
            console.print("[red]--migrate-mount 与 --check 互斥[/]")
            sys.exit(2)
        _run_migrate(args, config, console)
        return
    hostname = getattr(config, "hostname", "") or "<unknown>"
    declared = getattr(config, "host_declared", None)
    src = {True: "hosts 声明", False: "vars 基础值(hostname 未声明)",
           None: "vars 基础值(config 无 hosts 块)"}[declared]
    mode = "check(只读)" if args.check else "setup(幂等补建)"
    console.print(f"host: [bold]{hostname}[/]  路径来源: {src}  模式: {mode}")

    results = run_setup(config, apply=not args.check)

    table = Table(box=box.SIMPLE_HEAD, header_style="bold cyan", pad_edge=False)
    table.add_column("", justify="center")
    table.add_column("check", justify="left", no_wrap=True)
    table.add_column("detail", justify="left", overflow="fold")
    for r in results:
        mark = _MARK.get(r.status, "?")
        detail = f"{r.detail}(已补建)" if r.fixed else r.detail
        table.add_row(mark, r.title, detail, style=_STYLE.get(r.status, ""))
    console.print(table)

    n_fail = sum(1 for r in results if r.status == "fail")
    n_warn = sum(1 for r in results if r.status == "warn")
    n_fixed = sum(1 for r in results if r.fixed)
    console.print(f"FAIL: {n_fail}  WARN: {n_warn}  已补建: {n_fixed}  "
                  f"(共 {len(results)} 项;WARN 为角色相关,按本机职责判断)")
    if has_failures(results):
        sys.exit(1)


def _run_migrate(args, config, console):
    from ops.services.setup.jfs import MigrateError, actual_jfs_mount, migrate_mount

    target = config.alpha_src.parent
    try:
        mounts = Path("/proc/mounts").read_text()
    except OSError:
        mounts = ""
    found = actual_jfs_mount(mounts)
    current = found[0] if found else "(无 JuiceFS 挂载)"
    console.print(f"迁移计划: [bold]{current}[/] → [bold]{target}[/]\n"
                  "将改动: /etc/juicefs-poc.env、systemd unit(重渲染)、"
                  "sidecar 搬运、/mnt/storage/alphalib 重指;"
                  "旧址报告不删。失败自动回滚旧配置。")
    if not args.yes:
        answer = input("继续? [y/N] ").strip().lower()
        if answer != "y":
            console.print("已取消")
            return
    try:
        for line in migrate_mount(config):
            console.print(f"  {line}")
        console.print("[green]迁移完成[/] —— 接着跑 `ops setup` 补建布局、"
                      "`ops setup --check` 验收")
    except MigrateError as e:
        console.print(f"[red]迁移中止:[/] {e}")
        sys.exit(1)
