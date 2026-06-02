# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**ops** is a Python CLI for alpha factor validation, backtesting, and lifecycle management. It orchestrates a 7-stage validation pipeline for quantitative trading factors before they enter the production factor library.

## Commands

```bash
uv sync                              # Install dependencies (uses uv, not pip)
uv run ops --help                    # CLI help
uv run ops submit -u wbai -s 20260401            # Submit a day's factors from dropbox
uv run ops submit -u wbai -s 20260401 -f Alpha   # Submit one factor
uv run ops check                                 # Run 7-stage pipeline on staging
uv run ops status AlphaXxx                       # Query factor lifecycle state
uv run ops status -u wbai --status submitted     # Filter by author/state
uv run ops backfill --dry-run                    # Preview backfill on alpha_src/
uv run ops backfill                              # Generate meta.json + ACTIVE for legacy factors
uv run ops list                      # List factors in library (staging)
uv run ops list -c config.prod.yaml  # List factors in production library
uv run ops list --author wbai        # Filter by author
uv run ops list --refresh            # Force rebuild index cache
uv run ops list --format json        # JSON output
uv run ops info <factor-name>        # Show factor details
uv run ops health                    # Factor library health check
uv run ops health --fix              # Auto-refresh missing metrics/datasources
uv run ops pack                      # Aggregate alpha_dump → alpha_feature (skip already-packed)
uv run ops pack --force              # Rewrite all factors
uv run ops pack --factor AlphaXxx    # Pack one factor
uv run ops sync push                 # 两端 list+diff 增量推送(size + mtime 兜底)+ state merge
uv run ops sync push --dry-run       # Preview transfers
uv run ops sync push --deep          # 等大小再走 etag 比对捕捉内容漂移(慢)
uv run ops sync pull                 # state merge + 拉远端新增/更新的文件(按 status 过滤)
uv run ops sync pull --deep          # 同上,等大小走 etag
uv run ops sync status               # Quick local-vs-remote summary (no data scan)
uv run ops sync verify               # 三个数据目录两端文件级校验
uv run ops sync verify --deep        # 加 etag 校验,捕捉等大小漂移(慢,读全部本地文件)
uv run ops rm AlphaXxx               # 软删除:仅打 DELETED 标,文件保留
uv run ops rm AlphaXxx --force       # 同时删本地 dump + feature(保留 src/pnl)
uv run ops resubmit -u wbai -s 20260401 -f Alpha   # 已有因子提交新代码(version += 1)
uv run ops resubmit -u wbai -s 20260401            # 批量:该日期下所有已存在因子
uv run ops recheck AlphaXxx          # 原代码不变,重跑 check 流水线
uv run ops recheck AlphaXxx -s rejected   # 从 recycle 召回 rejected 因子
uv run ops recheck AlphaXxx -s deleted    # 复活 deleted 因子(soft-delete 仍保留 src)
uv run ops recheck AlphaXxx --purge  # 同时清除 dump + feature(保留 src/pnl)
uv run ops recheck -u wbai           # 批量:wbai 所有 active 因子(apt 风格确认)
uv run ops recheck -u wbai -y        # 批量,跳过确认
uv run ops approve AlphaXxx          # 人工通过 correlation 失败因子 (REJECTED → ACTIVE)
uv run ops approve -u wbai           # 批量:wbai 所有 correlation-rejected 因子
uv run ops approve -u wbai -y        # 批量,跳过确认
```

No test suite exists. Python 3.10+ required (see `.python-version`). Package manager is **uv** (not pip).

```bash
uv sync          # Install dependencies
uv add <pkg>     # Add new dependency
uv run <cmd>     # Run command in venv
```

## Architecture

Entry point: `ops/main.py` (argparse dispatcher). CLI registration in `ops/cli/*.py`, business logic in `ops/services/*/`.

Project is organized in 4 layers: `cli/` (argparse + output) → `services/` (orchestration) → `core/` (data models) + `infra/` (I/O, external systems). `utils/` for shared utilities.

| Subcommand | Purpose | Module |
|------------|---------|--------|
| `submit` | Copy new factors from dropbox to staging, generate `meta.json`, mark SUBMITTED | `ops/services/submit/` |
| `resubmit` | Existing factor with new code from dropbox, version += 1, mark SUBMITTED | `ops/services/resubmit/` |
| `recheck` | Move ACTIVE/REJECTED/DELETED factor back to staging for re-check (code unchanged) | `ops/services/recheck/` |
| `approve` | 人工审批 correlation 失败因子,REJECTED → ACTIVE(不重跑 check) | `ops/services/approve/` |
| `check` | 7-stage validation pipeline (runs in-place on staging) | `ops/services/check/` |
| `run` | Run backtest on factors in library | `ops/services/run/` |
| `status` | Query factor lifecycle state | `ops/services/status/` |
| `backfill` | One-shot: generate `meta.json` + ACTIVE for existing factors in `alpha_src/` | `ops/services/backfill/` |
| `list` | List factors in the library | `ops/cli/list.py` + `ops/services/list/` |
| `info` | Show factor details | `ops/cli/info.py` + `ops/services/info/` |
| `health` | Factor library health check | `ops/cli/health.py` + `ops/services/health/` |
| `pack` | Aggregate per-date `alpha_dump` files into per-factor `alpha_feature` matrices | `ops/cli/pack.py` + `ops/services/pack/` |
| `sync` | Push/pull factor library (data + state) across servers via S3 | `ops/cli/sync.py` + `ops/services/sync/` |

Removed subcommands: `cp`, `scp`, `compiler`.

### Design Principles

**Destructive operations are opt-in.** Default behavior never deletes user data. Every destructive path lives behind an explicit flag or a separate subcommand. Established patterns:

- `ops rm` defaults to state-only soft-delete. `--force` removes local dump + feature only.
- `ops resubmit` copies new code from dropbox to staging, version += 1.
- `ops sync push` is additive, never deletes remote objects.
- Bulk operations default to dry-run; require `--apply` (or equivalent) to execute.
- State merge prefers data preservation over precision: tied `updated_at` keeps local.

When adding a new command that touches files, state, or remotes: default to the non-destructive path, surface the destructive variant behind a flag, and require explicit user authorization at the scope being acted on.

### Dual Config Strategy

`config.yaml` points to staging paths for `ops check` output and review. `config.prod.yaml` points to production `/mnt/storage/alphalib/`. Pass `-c config.prod.yaml` to any command to target production.

## Key Concepts

### Gsim Backtest Framework

Located at `/usr/local/gsim/`. The core backtesting engine that ops interacts with.

```bash
/usr/local/gsim/.venv/bin/python /usr/local/gsim/run.py config.xml          # backtest
/usr/local/gsim/.venv/bin/python /usr/local/gsim/tools/simsummary.py /pnl   # PNL summary
/usr/local/gsim/dataops/bcorr pnl1 pnl2                                    # correlation
```

### User Factor Workspace

```
/mnt/storage/dropbox/{unix_id}/{yyyymmdd}/Alpha{UnixId}{FactorName}/
```

### Factor Library Structure

```
/mnt/storage/alphalib/
├── alpha_src/      # Factor source code        — alpha_src/<name>/        目录
├── alpha_pnl/      # Backtest results (PNL)    — alpha_pnl/<name>         单文件 ⚠
├── alpha_dump/     # Daily target positions    — alpha_dump/<name>/       目录(内含每日小文件)
└── alpha_feature/  # Aggregated alpha_dump     — alpha_feature/<name>.{v}.npy  单文件
```

**⚠ alpha_pnl/<name> 是单文件,不是目录**。删除用 `Path.unlink()`,不要用 `shutil.rmtree()`(`Errno 20: Not a directory`)。alpha_feature 同理是单文件。只有 alpha_src / alpha_dump 是目录。

### Factor Directory Structure

Each factor in `alpha_src/` contains:
```
AlphaXxx/
├── AlphaXxx.py           # Factor code (inherits gsim.AlphaBase)
├── Config.Xxx.xml        # Gsim config file
└── Readme.Xxx.txt        # Backtest report
```

**Data Source Tracking**: DO NOT trust XML `<Data>` declarations — parse Python code for actual `dr.getData('xxx')` calls.

## Key Dependencies

- **paramiko** / **scp** - SSH connections and file transfer
- **pandas** / **numpy** - Data processing
- **lxml** / **xmltodict** - XML config manipulation
- **colorama** - Terminal colors
- **tqdm** - Progress bars
- **pyyaml** - Config parsing
- **boto3** - S3-compatible object storage (sync)

## Known Technical Debt (Deferred)

- **Stub files**: `core/alpha/results/base.py`, `results/checkpoint.py`, `results/checkbias.py`
- **Dead code**: `infra/notify/email.py` is commented out
- **Debug residual**: `utils/func.py` has a `debug()` with infinite loop
- **Feishu credentials hardcoded**: `infra/notify/feishu_send.py` — move to config/env later
- **`core/alpha/metadata.py` has I/O**: `_modify_always()`, `save()`, `get_v2npy_files()` — extract to services/infra

## Plans & Roadmap

See `docs/factor-state-machine.md` for the factor lifecycle design (state definitions, transitions, data product rules, version control direction).

### Factor State Machine Refactor (已修复)

以下问题已在 75ded5d 中修复:

1. ~~submit 未拒绝已存在因子~~ → `submit_one` 开头检查 `store.get()`,存在则拒绝
2. ~~submit 有 recycle fallback~~ → 已删除,已有因子走 resubmit/recheck
3. ~~recheck REJECTED 代码来源错误~~ → `_locate_source` 对 REJECTED 统一从 alpha_src 拿
4. ~~recheck REJECTED 产物未自动清理~~ → REJECTED recheck 自动清 pnl/dump/feature
5. ~~to_recycle 未按失败阶段区分产物~~ → compliance/correlation 保留 dump+pnl 并生成 feature;checkbias/checkpoint 清掉

### Sync Storage Optimization

**Phase 2 — gsim FeatureReader + sync 瘦身（已完成）**: alpha_dump 降级为纯本地中间产物，sync 只传 alpha_src / alpha_pnl / alpha_feature / .state

**Phase 3 — ops pack 增量模式（待实现）**: `ops pack --date YYYYMMDD`、PACK_L 动态化、并发安全

**Phase 4 — Alphalib 存储后端打通(长期)**: 按数据类别分别用 Git / JuiceFS / DB 替代当前 sync 模型。详见 `.claude/plans.md` 的 "Alphalib Storage Backend Migration"。
