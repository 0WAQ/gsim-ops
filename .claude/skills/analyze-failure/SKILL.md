---
name: analyze-failure
description: Debug why a factor failed in the check pipeline
---

# Analyze Failure

Deep-dive into why a factor was rejected by the check pipeline.

## Investigation steps

1. **Get factor state**
   - Run `uv run ops status <name>` to see current status
   - If REJECTED, note `last_fail_stage` and `last_fail_reason`
   - Review `check_history` for patterns (always fails at same stage?)

2. **Locate factor files**
   - REJECTED 因子的 src 归档在 `alpha_src/<name>/`（靠 state 的 status/last_fail_stage 区分，无 recycle 目录）
   - If in `staging/`, it's a validate/long_backtest failure (retriable)
   - Read the factor's `.py` and `.xml` files

3. **Stage-specific diagnosis**

   **validate / long_backtest failure**:
   - Environmental/config issue, not factor quality
   - Check if XML paths are correct
   - Look for missing data dependencies
   - Suggest: `ops check --retry`

   **checkbias failure**:
   - Forward-looking data access detected
   - Read the factor code, look for `data[di]` access patterns
   - Check delay value in XML
   - Explain which line triggered the firewall

   **checkpoint failure**:
   - Breakpoint instability (5-day checkpoint mismatch)
   - Factor uses non-deterministic operations or time-dependent logic
   - Suggest: review operations chain, check for random seeds

   **compliance failure**:
   - Position limits violated (max 5% per stock) or min stock counts not met
   - Run `uv run ops info <name>` to see if PNL exists
   - If PNL exists, could check dump files for position distribution

   **correlation failure**:
   - Factor too similar to existing library (corr >= 0.7)
   - Suggest: check which existing factor it correlates with
   - May need to adjust factor logic or operations

   **archive failure**:
   - simsummary failed or file move error
   - Check if PNL directory exists and is readable

4. **Crash-mid-check self-heal**
   - reconcile 已下线，无 state/filesystem 对账。若因子 check 中途崩溃（state 停在 CHECKING），
     下一次 `ops check` 扫 staging 时自动重跑，无需仲裁。

5. **Actionable recommendations**
   - If retriable (validate/long_backtest): `ops check --retry`
   - If factor quality issue: specific code changes needed
   - If need to recall a rejected factor for re-check: `ops restage <name> -s rejected`

## Output format

```
Factor: AlphaXxx
Status: REJECTED
Failed at: checkbias
Reason: Forward-looking access detected

Root cause:
  Line 45: self.close[di] accessed when delay=1
  
Fix:
  Change to self.close[di-1] or self.close[:di]
  
Next steps:
  1. QR 改代码后经 dropbox 重新 submit（`ops submit --overwrite`），或 `ops restage AlphaXxx -s rejected` 原代码重跑
  2. ops check
```

## Usage

```
/analyze-failure AlphaWbai20260531Test
```
