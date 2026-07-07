---
name: project_uv_tool_env_deps
description: "生产 ops 走 uv tool install 独立环境, 新增依赖要重装 tool 否则 ModuleNotFoundError"
metadata: 
  node_type: memory
  type: project
  originSessionId: 6171a8a8-01f1-4004-bcd4-cef65b017af2
---

生产用户敲的 `ops` 命令走 `uv tool install` 装的**独立工具环境** (`~/.local/share/uv/tools/ops/bin/python3`, shebang 在 `~/.local/bin/ops`), 跟项目 `.venv` 是两套 Python。

**坑**: `uv add <pkg>` 只装进项目 `.venv`, 不进 tool 环境。给 ops 加新依赖 (如 2026-07 加 psycopg 时) 后, 项目里 `uv run` 能 import, 但真 `ops` 命令报 `ModuleNotFoundError` —— 因为跑的是 tool 环境。

**How to apply**:
- 测试改动时用 `.venv/bin/python -m ops.main <args>` (走项目 venv, 有新依赖), 别用 `ops` / `uv run ops` (会解析到 PATH 里的 tool 环境入口)。
- 加依赖后部署: `uv add <pkg>` (进 venv) + `uv tool install --reinstall .` 或等价重装 (同步进 tool 环境)。
- 三机 (160/150/144) 各自的 tool 环境独立, 每台都要重装。

**Why**: uv tool install 故意隔离工具依赖, 避免污染。代价是依赖要显式同步两处。相关: [[project_factor_library_storage_architecture]]
