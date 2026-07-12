"""ops doctor —— 盘 ↔ PG 数据对账(缺省纯只读;修复按族点名 + 逐族确认)。"""
import argparse
import json
import shutil
import sys
from dataclasses import asdict

from rich import box
from rich.console import Console
from rich.table import Table

from ops.cli.common import add_config_arg, load_config
from ops.services.doctor import (
    FAMILY_IDS,
    FIXABLE_IDS,
    DoctorUnavailable,
    fail_residual,
    run_doctor,
)
from ops.services.doctor.findings import FIXED, LOCKED, VANISHED

_SEV_STYLE = {"fail": "red", "warn": "yellow"}
_CONFIRM_LIST_MAX = 50


class _FixAction(argparse.Action):
    """--fix <family>:收集修复族,同时声明写性(sudo self-elevate 据
    is_write_command 提权 —— 只读报告绝不 sudo,是 setup _CheckAction 的反相)。"""

    def __call__(self, parser, namespace, values, option_string=None):
        items = list(getattr(namespace, self.dest, None) or [])
        items.append(values)
        setattr(namespace, self.dest, items)
        namespace.is_write_command = True


def add_doctor_subparser(subparsers: argparse._SubParsersAction):
    parser: argparse.ArgumentParser = subparsers.add_parser(
        "doctor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        help="盘 ↔ PG 数据对账(缺省只读报告;--fix <族> 修复)",
        epilog=f"""\
Example:
    ops doctor                        # 全族只读对账报告(零 sudo、零写)
    ops doctor --family pool-ghost    # 只看指定族(可重复)
    ops doctor --fix snapshot-stale   # 修复指定族(先扫描,逐族确认后执行)
    ops doctor --format json          # 全量明细 JSON(人读表格转 stderr)

检查族: {', '.join(FAMILY_IDS)}
可修复: {', '.join(FIXABLE_IDS)}(其余 report-only,报告内附转介命令)
scope=host 的族(dump-orphan)只看本机 sidecar —— 各机各跑。
退出码: 0=无 FAIL 级漂移;1=有(--fix 后按余量);2=用法错误/PG 不可达。
族注册表: ops/services/doctor/checks.py(新增族 = 加一行)。
""",
    )
    parser.add_argument("--family", action="append", choices=list(FAMILY_IDS),
                        metavar="FAMILY", default=None,
                        help=f"族过滤,可重复(choices: {', '.join(FAMILY_IDS)})")
    parser.add_argument("--fix", action=_FixAction, choices=list(FIXABLE_IDS),
                        metavar="FAMILY", default=[],
                        help=f"按族修复,可重复(choices: {', '.join(FIXABLE_IDS)};"
                             "逐族独立确认)")
    parser.add_argument("-y", "--yes", action="store_true",
                        help="跳过修复确认(cron 用;交互环境请勿)")
    parser.add_argument("--format", choices=["table", "json"], default="table",
                        help="输出格式(json 时明细全量到 stdout,人读输出转 stderr)")
    parser.add_argument("--limit", type=int, default=20,
                        help="每族终端明细最多显示行数(默认 20;全量看 --format json)")
    add_config_arg(parser)
    # 缺省只读(不调 mark_write);--fix 经 _FixAction 声明写性
    parser.set_defaults(func=run_doctor_cli, is_write_command=False)


def run_doctor_cli(args):
    config = load_config(args.config_path)
    json_mode = args.format == "json"
    # json 到 stdout(可管道/落盘),人读输出走 stderr —— 两不相扰
    console = Console(stderr=True) if json_mode else Console(
        width=shutil.get_terminal_size((140, 50)).columns)

    fix: tuple[str, ...] = tuple(args.fix or ())
    if fix and args.family is not None:
        missing = [f for f in fix if f not in args.family]
        if missing:
            console.print(f"[red]--fix {' '.join(missing)} 不在 --family 选择内[/]")
            sys.exit(2)
    if fix and not args.yes and not sys.stdin.isatty():
        console.print("[red]非交互环境的修复必须显式 -y(确认是逐族授权,"
                      "不能静默跳过)[/]")
        sys.exit(2)

    def confirm(result, fixer) -> bool:
        fixables = [f for f in result.findings if f.fixable]
        console.print(f"\n[bold]修复确认 · {result.family_id}[/]"
                      f"(待处理 {len(fixables)} 条)")
        # FixPlan 逐字打印 —— 打印的就是执行的(白名单执行器只认注册 action)
        console.print(f"  动作   : {fixer.plan.action}")
        console.print(f"  删什么 : {fixer.plan.target}")
        console.print(f"  不碰   : {fixer.plan.keeps}")
        for f in fixables[:_CONFIRM_LIST_MAX]:
            console.print(f"    - {f.name}  [{f.kind}] {f.path or ''}")
        if len(fixables) > _CONFIRM_LIST_MAX:
            console.print(f"    …另有 {len(fixables) - _CONFIRM_LIST_MAX} 条"
                          "(全量见 --format json)")
        if args.yes:
            console.print("  [dim](-y 已跳过交互确认)[/]")
            return True
        answer = input(f"确认修复 {result.family_id}? [y/N] ").strip().lower()
        return answer == "y"

    try:
        inv, results = run_doctor(config, families=args.family, fix=fix,
                                  confirm=confirm)
    except DoctorUnavailable as e:
        console.print(f"[red]doctor 无法运行:[/] {e}")
        sys.exit(2)
    except NotImplementedError as e:
        console.print(f"[red]doctor 需要 postgres 后端:[/] {e}")
        sys.exit(2)

    mode = "修复(--fix " + ",".join(fix) + ")" if fix else "只读报告"
    console.print(f"\nhost: [bold]{inv.hostname}[/]  模式: {mode}  "
                  f"PG 因子总数: {len(inv.factors)}")
    console.print("[dim]scope=host 的族只看本机 sidecar(dump 每机一份,"
                  "各机各跑);与在跑 check 并发时瞬态漂移(missing/checking 类)"
                  "属正常,只报不删[/]")

    _render_summary(console, results)
    _render_details(console, results, args.limit)
    if json_mode:
        print(json.dumps(_to_json(inv, results, mode), indent=2,
                         ensure_ascii=False, default=str))

    n_fail = fail_residual(results)
    if n_fail:
        console.print(f"[red]FAIL 级漂移余量: {n_fail}[/]")
        sys.exit(1)


def _render_summary(console, results):
    table = Table(box=box.SIMPLE_HEAD, header_style="bold cyan", pad_edge=False)
    _right = {"checked", "fail", "warn", "fixable", "fixed", "locked"}
    for col in ("family", "scope", "checked", "fail", "warn", "fixable",
                "fixed", "locked", "note"):
        table.add_column(col, justify="right" if col in _right else "left")
    for r in results:
        if r.skip_reason:
            table.add_row(r.family_id, r.scope, "-", "-", "-", "-", "-", "-",
                          f"skip: {r.skip_reason}", style="dim")
            continue
        n_fail = sum(1 for f in r.findings if f.severity == "fail")
        n_warn = sum(1 for f in r.findings if f.severity == "warn")
        n_fixable = sum(1 for f in r.findings if f.fixable)
        style = "red" if r.residual("fail") else ("yellow" if n_warn else "green")
        table.add_row(r.family_id, r.scope, str(r.population), str(n_fail),
                      str(n_warn), str(n_fixable), str(r.fixed),
                      str(r.count(LOCKED)), r.title, style=style)
    console.print(table)


def _render_details(console, results, limit: int):
    outcomes = {}
    for r in results:
        for f, o, err in r.fix_log:
            outcomes[(f.name, f.kind, f.ref)] = (o, err)
    for r in results:
        if r.skip_reason or not r.findings:
            continue
        console.print(f"\n[bold cyan]{r.family_id}[/] · {r.title}"
                      f"(共 {len(r.findings)} 条)")
        for f in r.findings[:limit]:
            sev = f"[{_SEV_STYLE.get(f.severity, '')}]{f.severity}[/]"
            line = f"  {sev} [{f.kind}] {f.name} — {f.reason}"
            o = outcomes.get((f.name, f.kind, f.ref))
            if o:
                mark = {FIXED: "[green]已修复[/]", LOCKED: "[yellow]锁跳过[/]",
                        VANISHED: "[dim]已消失[/]"}.get(o[0], f"[red]{o[0]}[/]")
                line += f"  → {mark}" + (f"({o[1]})" if o[1] and o[0] != FIXED else "")
            elif f.action:
                line += f"  → {f.action}"
            console.print(line)
        if len(r.findings) > limit:
            console.print(f"  [dim]…另有 {len(r.findings) - limit} 条,"
                          "见 --format json[/]")


def _to_json(inv, results, mode: str) -> dict:
    return {
        "host": inv.hostname,
        "mode": mode,
        "pg_total": len(inv.factors),
        "families": [{
            "family_id": r.family_id,
            "title": r.title,
            "scope": r.scope,
            "population": r.population,
            "skip_reason": r.skip_reason,
            "findings": [asdict(f) for f in r.findings],
            "fix_log": [{"name": f.name, "kind": f.kind, "ref": f.ref,
                         "outcome": o, "err": e} for f, o, e in r.fix_log],
        } for r in results],
    }
