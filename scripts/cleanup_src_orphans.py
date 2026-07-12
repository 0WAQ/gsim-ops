#!/usr/bin/env python3
"""一次性处置:删除 alpha_src 下 PG 全无记录的孤儿源码目录(doctor v1.1 A 批)。

背景(2026-07-12,DOCTOR-V11-TRIAGE):107 个 src-orphan(完整因子目录、PG
零记录、零产物侧影)经基线判读确认为历史清理残渣,用户拍板直接删除。

**为什么是一次性脚本而不是 doctor --fix**:alpha_src 是源码唯一副本、全库
爆炸半径最大的删除对象 —— doctor 的 v1 铁律是 alpha_src 永不进删除集
(误判即毁掉不可再生的代码)。本脚本是"判读后的名单化处置",不是常规通道;
Phase D(alpha_src 上 git)落地后这类残渣有历史可回滚,才谈得上常规化。

用法(160,sudo;默认 dry-run):
    uv run ops doctor --family src-drift --format json > /tmp/doctor.json 2>/dev/null
    sudo $(command -v uv) run python scripts/cleanup_src_orphans.py --input /tmp/doctor.json
    sudo $(command -v uv) run python scripts/cleanup_src_orphans.py --input /tmp/doctor.json --apply

逐目录守卫(照 doctor guards 纪律):
    factor_lock 非阻塞(拿不到跳过)→ 锁内 repo.get 复核 PG 仍无记录 →
    名字不在 staging(防召回/提交竞态)→ 目标是 alpha_src/<name> 真实目录
    非软链 → rmtree。
安全面:只删名单内、复核通过的 alpha_src/<name>/ 目录;不碰 PG、不碰其它区。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ops.infra.config import Config, get_default_config_path  # noqa: E402
from ops.infra.lock import FactorLocked, factor_lock  # noqa: E402
from ops.infra.repository import FactorRepository  # noqa: E402


def load_names(report_path: Path) -> list[str]:
    report = json.loads(report_path.read_text())
    return sorted({
        f["name"]
        for fam in report.get("families", [])
        if fam.get("family_id") == "src-drift"
        for f in fam.get("findings", [])
        if f.get("kind") == "src-orphan"
    })


def cleanup_one(name: str, config, repo: FactorRepository, apply: bool) -> tuple[str, str]:
    """返回 (outcome, detail)。outcome ∈ {removed, would-remove, skip, error}。"""
    target = config.alpha_src / name
    real = target.parent.resolve() / target.name
    if real.parent != config.alpha_src.resolve():
        return "error", f"目标越界: {real}"
    try:
        with factor_lock(name, config):
            if repo.get(name) is not None:
                return "skip", "PG 已有记录(并发 submit/backfill 已登记),不删"
            if (config.staging / name).exists():
                return "skip", "staging 有同名目录(在途因子),不删"
            if real.is_symlink():
                return "skip", "目标是软链,不删"
            if not real.is_dir():
                return "skip", "目录已不存在"
            n_files = sum(1 for _ in real.iterdir())
            if not apply:
                return "would-remove", f"{n_files} 个文件"
            import shutil
            shutil.rmtree(real)
            return "removed", f"{n_files} 个文件"
    except FactorLocked:
        return "skip", "因子锁被持有,跳过(重跑再收)"
    except OSError as e:
        return "error", str(e)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="删除名单内 PG 全无记录的 alpha_src 孤儿目录(默认 dry-run)")
    parser.add_argument("--input", type=Path, required=True,
                        help="ops doctor --format json 报告(取 src-drift/src-orphan 名单)")
    parser.add_argument("--config-path", "-c", type=Path,
                        default=get_default_config_path())
    parser.add_argument("--apply", action="store_true",
                        help="执行删除(默认 dry-run 只列计划)")
    args = parser.parse_args()

    names = load_names(args.input)
    if not names:
        print("名单为空(报告里无 src-drift/src-orphan),无事可做")
        return 0
    print(f"名单(src-orphan): {len(names)} 条;模式: "
          f"{'APPLY' if args.apply else 'dry-run'}")

    config = Config.load(args.config_path)
    repo = FactorRepository(config)

    counts: dict[str, int] = {}
    failed = 0
    for name in names:
        outcome, detail = cleanup_one(name, config, repo, args.apply)
        counts[outcome] = counts.get(outcome, 0) + 1
        mark = {"removed": "✔", "would-remove": "·", "skip": "-", "error": "✘"}[outcome]
        print(f"  {mark} {name}: {outcome} ({detail})")
        if outcome == "error":
            failed += 1

    print(f"\n汇总: {counts}")
    if not args.apply:
        print("dry-run 结束(未删除任何目录;执行加 --apply)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
