---
name: analyze-report
description: Analyze an ops check run report — batch summary + forwardable QR feedback
---

# Analyze Report

Analyze a whole `ops check` run: summarize pass/fail distribution, which stage
factors failed at, and draft a clean feedback block to hand back to QR.

This is **batch-level**. For single-factor root cause, use `/analyze-failure` or
the `pipeline-debugger` agent instead.

## What to do

1. **Locate the data source**

   **A. Structured JSON report (preferred)** — `docs/reports/check/check-<scope>-<ts>.json`
   (repo-relative; `<scope>` = factor name | user | `all`).

   - No argument → latest report overall:
     ```bash
     ls -t docs/reports/check/check-*.json | head -1
     ```
   - Argument is a user (e.g. `wbai`) → latest for that scope:
     ```bash
     ls -t docs/reports/check/check-wbai-*.json | head -1
     ```
   - Argument is a full path → use it directly.

   **B. Fallback — `~/.cache/ops/logs/ops.log`** — use when:
   - no JSON exists for the target (runs from before the report feature, commit d245fe8), or
   - the user asks about a specific person across history (`/analyze-report xmf` with no JSON).

   The agent knows how to parse ops.log (filter `factor=<user>/`, strip ANSI, split
   retryable=error vs rejected=fail). Just tell it which user + that the source is ops.log.

   Nothing in either place → tell the user to run `ops check` first. Do not fabricate.

2. **Delegate to the report-analyst agent**

   Spawn the `report-analyst` agent (Agent tool, `subagent_type: report-analyst`),
   passing the resolved report file path (source A) or the target user + "source is
   ops.log" (source B) in the prompt. It reads the data and produces the feedback.

3. **Present the result**

   Relay the agent's output. It is meant to be copy-pasted / forwarded as-is, so keep
   it clean: Chinese, no emoji, **no letter tone** (no 称呼/问候/落款). Output leads
   with three timestamps — dropbox 日期 / submit / check — then the feedback.

## Output shape (from the agent)

- 三个时间锚点:预提交(dropbox yyyymmdd)/ submit / check
- 跑不起来的(validate/error,环境或数据依赖问题):按根因归类 + 数量 + 代表因子
- 跑完被打回的(fail,质量问题,改代码 `submit --overwrite`):按 stage/根因归类 + 完整 fail_reason

## Usage

```
/analyze-report              # latest JSON report overall
/analyze-report wbai         # latest JSON for wbai; if none, fall back to ops.log
/analyze-report xmf          # a user with no JSON yet → analyze from ops.log
/analyze-report docs/reports/check/check-wbai-20260702-113331.json   # specific file
```
