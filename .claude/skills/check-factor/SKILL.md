---
name: check-factor
description: Analyze a factor's code quality, data dependencies, and potential issues
---

# Check Factor

Perform a comprehensive analysis of a factor before submission or when debugging failures.

## What to analyze

1. **Code Quality**
   - Read the factor's `.py` file
   - Check for common anti-patterns: hardcoded dates, magic numbers, unused imports
   - Verify `generate(di)` signature and return type
   - Look for potential forward-looking bias (accessing `data[di]` when delay >= 1)

2. **Data Dependencies**
   - Extract all `dr.getData('...')` calls via AST or grep
   - Check if declared data modules in XML match actual usage
   - Verify data sources exist in `/datasvc/data/cc/`
   - Flag L2 data usage (needs special niodatapath handling)

3. **Configuration**
   - Read XML config
   - Verify delay value (0 or 1)
   - Check universe, backdays, operations chain
   - Ensure `Portfolio.Alpha.@id` matches directory name

4. **Metadata**
   - If `meta.json` exists, validate schema version and required fields
   - Check author resolution (XML vs inferred from dir name)

5. **State & History**
   - Query factor state via `ops status <name>`
   - If REJECTED, show last_fail_stage and reason
   - If has check_history, summarize past failures

## Output format

Provide a structured report:
- ✅ Passed checks
- ⚠️  Warnings (non-blocking but worth attention)
- ❌ Issues (likely to cause check failure)
- 📊 Summary: data sources, delay, operations

## Usage

```
/check-factor AlphaWbai20260531Test
```

If no factor name provided, ask the user which factor to analyze.
