---
agent: agent
description: "Validate the entire strata workspace and fix any issues found"
---

Validate all YAML configuration files in this strata workspace and help fix any issues.

Steps:
1. Check workspace readiness:
```bash
strata guide show
```

2. Run full validation:
```bash
strata validate --all --output json
```

3. If any files fail validation (exit code 3):
   - Read the error details for each file
   - Open each failing file and identify the issue
   - Fix the errors (common issues: unknown fields, invalid names, broken cross-references)
   - Re-validate until all files pass

4. If repos are not synced:
```bash
strata repo status
```

5. Report results:
   - Number of files validated
   - Issues found and fixed
   - Current guide phase (from `strata guide show`)
   - Recommendations for next steps
