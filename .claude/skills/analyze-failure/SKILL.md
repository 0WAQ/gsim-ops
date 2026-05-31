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
   - Check if factor is in `recycle/{user}/{stage}/` (expected for REJECTED)
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

4. **Reconciliation check**
   - If state and filesystem location mismatch, explain reconcile would fix it
   - Show the reconcile table from `ops/services/check/CLAUDE.md`

5. **Actionable recommendations**
   - If retriable (validate/long_backtest): `ops check --retry`
   - If factor quality issue: specific code changes needed
   - If need to resubmit: `ops resubmit <name> -s rejected`

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
  1. Fix the code in recycle/wbai/checkbias/AlphaXxx/
  2. ops resubmit AlphaXxx -s rejected
  3. ops check
```

## Usage

```
/analyze-failure AlphaWbai20260531Test
```
