#!/usr/bin/env python3
"""C2/C3 import 契约的 ratchet 基线断言:违例数只许降、不许升。

全绿契约在 pyproject [tool.importlinter] 直接 enforcing;本脚本只管还有存量
违例的 C2(cli 直接 import infra/core)与 C3(service 包互相独立)。某契约
清零后,把它移入 pyproject 并从 contracts-baseline.toml + BASELINE 删除。

fail-closed 纪律(2026-07-09 对抗评审揪出首版 fail-open,JOURNAL U3):门禁
脚本自己不能被无声解除 ——
- lint-imports 只有 0(全 kept)/1(有 broken)两种合法退出码,其它一律视为
  工具故障直接挂;
- BASELINE 里每个契约头必须在输出中出现过(KEPT/BROKEN 任一形式),缺失说明
  配置漂移(契约改名 / 硬编码包名失效 / 配置文件丢失),直接挂;
- 未知的 C\\d+ 契约头重置归属,防止新契约的违例被记到别人头上。

本地运行:uv run python scripts/ci/import_baseline.py
"""
import re
import subprocess
import sys

# 2026-07-09 实测基线(docs/factor-aggregate-plan.md §2.3)
BASELINE = {"C2": 18, "C3": 9}

proc = subprocess.run(
    ["lint-imports", "--config", "contracts-baseline.toml"],
    capture_output=True, text=True,
)
out = proc.stdout + proc.stderr
print(out)

# 工具故障 ≠ 契约违例:0=全 kept、1=有 broken,其它(配置找不到 / TOML 语法错 /
# grimp 崩溃)必须挂,不能让 counts=0 假装达标。
if proc.returncode not in (0, 1):
    print(f"✘ lint-imports 异常退出(rc={proc.returncode}),视为工具故障")
    sys.exit(1)
if "Contracts:" not in out:
    print("✘ 输出没有 'Contracts:' 汇总行 —— lint-imports 没有真正完成分析")
    sys.exit(1)

counts = dict.fromkeys(BASELINE, 0)
seen: set[str] = set()
current = None
for line in out.splitlines():
    m = re.match(r"^(C\d+) ", line)
    if m:
        # 任何契约头都切换归属;未知头置 None,其违例不得记到别人名下
        current = m.group(1) if m.group(1) in counts else None
        if current:
            seen.add(current)
    elif current and re.match(r"^-\s+\S", line):
        counts[current] += 1

failed = False
for key in BASELINE:
    if key not in seen:
        print(f"✘ 契约 {key} 未在输出中出现 —— contracts-baseline.toml 漂移"
              f"(契约改名 / 包名失效?),门禁已失效,必须先修配置")
        failed = True

for key, base in BASELINE.items():
    print(f"{key}: {counts[key]} 条违例(基线 {base})")
    if counts[key] > base:
        print(f"✘ {key} 超过基线 —— 引入了新的跨层/跨包依赖;请消除依赖,不要上调基线")
        failed = True
    elif key in seen and counts[key] < base:
        print(f"⚠ {key} 低于基线 —— 请把 BASELINE 更新为 {counts[key]}(清零则转 enforcing)")

sys.exit(1 if failed else 0)
