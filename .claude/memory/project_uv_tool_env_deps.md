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
- 验证分支/未合并代码时用 `uv run python -m ops.main <args>` (走项目 venv, 有分支代码 + 新依赖)。**别用 `ops` / `uv run ops`**:项目 not packaged, `uv run ops` 不装 entry point → **fall through 到 PATH 上 `~/.local/bin/ops` 全局 tool 旧 shim**, 测的是已部署版本不是分支代码 (2026-07-13 legacy 批实测抓获: 分支已退役 `ops backfill` 但 `uv run ops backfill --help` 仍打正常 usage)。
- **未合并分支绝不 `uv tool install --reinstall` 成全局命令** —— 生产 `ops` 跟 main 走, 滚存在 PR 合并后。
- 加依赖后部署 / 滚存: `uv add <pkg>` (进 venv) + `uv tool install --reinstall .` (同步进 tool 环境)。
- **四机** (160/150/170/144) 各自 tool 环境独立, 每台都要重装 (170 是 2026-07 新增消费机)。

**非交互 ssh 滚存三坑** (2026-07-13 legacy 批四机实测):
1. **PATH 不加载**: uv 与全局 ops 都在 `~/.local/bin`, 非交互 ssh 不 source profile → 命令找不到, 须显式 `export PATH="$HOME/.local/bin:$PATH"`。
2. **CWD 依赖 config.yaml**: ops 靠当前目录找 `config.yaml`, ssh 默认落 `$HOME` → `FileNotFoundError: ~/config.yaml`, 必须先 `cd ~/gsim-ops`。
3. **144 WAN 节点首次 TLS 断连**: 跨段路由 (10.6 ↔ IDC), git pull 首次可能 TLS 断, 重试一次即过, 符合拓扑预期。

**Why**: uv tool install 故意隔离工具依赖, 避免污染。代价是依赖要显式同步两处, 且 not-packaged 项目下 `uv run ops` 会漏到全局 shim。相关: [[project_factor_library_storage_architecture]]
