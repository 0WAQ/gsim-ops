---
name: review-staging
description: Review all factors in staging before running ops check
---

# Review Staging

Batch review all factors currently in staging, ready for check pipeline.

## What to do

1. **List staging factors**
   - Scan `staging/` directory
   - For each factor, read `meta.json` if exists
   - Group by author

2. **Quick health check per factor**
   - Verify `.py` and `.xml` files exist (exactly 1 each)
   - Check XML `Portfolio.Alpha.@id` matches directory name
   - Extract delay value from XML
   - Count `dr.getData()` calls (rough complexity indicator)
   - Check if author is in `_GENERIC_AUTHORS` (needs manual review)

3. **State consistency**
   - Run `uv run ops status --status submitted` to see all SUBMITTED factors
   - Cross-check: factors in staging should be SUBMITTED
   - Flag any mismatches (reconcile would fix, but good to know)

4. **Risk assessment**
   - Flag factors with delay=0 (pack offset bug affects them)
   - Flag factors with > 20 data sources (complex, higher failure risk)
   - Flag factors from new authors (may not follow conventions)

5. **Summary report**

```
Staging Review (5 factors)

Ready to check:
  ✅ AlphaWbai20260531A    delay=1  author=wbai     12 data sources
  ✅ AlphaWbai20260531B    delay=1  author=wbai     8 data sources
  
Needs attention:
  ⚠️  AlphaFguo20260531X   delay=0  author=fguo     (pack offset bug)
  ⚠️  AlphaUnknown123      delay=1  author=unknown  (generic author, verify)
  
Blocked:
  ❌ AlphaWbai20260530Old  (missing .xml file)

Recommendation:
  - Fix AlphaWbai20260530Old before running ops check
  - Review AlphaUnknown123 author metadata
  - Proceed with ops check for the 2 ready factors
```

## Usage

```
/review-staging
```

Optionally filter by author:
```
/review-staging wbai
```
