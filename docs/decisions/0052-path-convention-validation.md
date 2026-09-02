# Path Convention Validation (`spec.paths`)

- Status: implemented — extended by [ADR 0072](./0072-clarify-layering-vs-path-convention.md)
- Date: 2026-07-23
- Parent: [ADR 0042 — Deep Validation and Layer Consistency](./0042-deep-validation-layer-consistency.md)

> **Extended by [ADR 0072](./0072-clarify-layering-vs-path-convention.md).** `spec.paths`
> remains as described here, plus: `resolves` accepts `layers` (in addition to `tenant`),
> conventions gain an inline `segments` list, and `validate:` rules now check each
> segment's **resolved** value (explicit → path-derived → default) rather than only the
> raw `match_pattern()` capture.

## Implementation Notes

**Completed 2026-07-23. All 45 tests passing (4607 total suite, 0 regressions).**

Key implementation detail: the `validate` field in `PathConventionModel` was renamed to `rules`
in Python to avoid a Pydantic attribute shadow conflict with `PlatformBaseModel`. The YAML alias
`validate` is preserved via `Field(alias="validate")` + `ConfigDict(populate_by_name=True)` so
existing YAML declarations using `validate:` continue to work.

**New files:**
- `src/strata/models/configuration_model.py` — `PathConventionModel` + `spec.paths` on `ConfigurationSpecModel`
- `src/strata/utils/path_convention.py` — `match_pattern`, `resolve_spec_rule`, `evaluate_file_rule`, `evaluate_conventions`
- `src/strata/validators/policies/path_convention_policy.py` — `PathConventionPolicy`
- `tests/strata/validators/test_path_convention_policy.py` — 45 tests

**Modified files:**
- `src/strata/validators/policies/base_policy.py` — added `file_path: Optional[Path]` to `PolicyContext`
- `src/strata/validators/policies/policy_engine.py` — registered `path_convention` type
- `src/strata/models/policy_model.py` — added `path_convention` to type description

---

## Context and Problem Statement

Fleet-scale deployments (ADR 0038) rely on consistent directory structures to enable tooling,
automation, and human navigation. Today nothing enforces that files placed in the repository
follow the intended hierarchy — only the operator's discipline and code review.

A configuration repository might declare:

```
zones/{zone}/customers/{tenant}/{env}/deploy.yaml
customers/{tenant}/tenant.yaml
landscape/{name}/landscape.yaml
```

But nothing stops someone from creating `zones/typo/customers/unknownco/deploy.yaml` where
`typo` isn't a declared zone and `unknownco` has no tenant registration file.

**`spec.paths`** introduces declarative path conventions on the configuration model — each
convention targets a subtree via a glob scope and defines the expected directory structure
and per-segment validation rules. A `path_convention` policy type enforces these at
validation time.

## Design Goals

1. **Declarative over imperative** — operators define conventions in YAML, not custom scripts
2. **Scoped** — different subtrees can have different conventions (like `spec.layerings`)
3. **Composable** — multiple conventions can match the same file; evaluated independently
4. **Graceful** — partial validation (file existence only) works without `--deep` mode;
   `spec.*` rules require configuration service (deep mode)
5. **Policy-driven enforcement** — deny/warn/audit via standard policy model, not hardcoded
6. **Deploy-repo friendly** — inline convention on policy for repos without a configuration model

## Relationship to `spec.layerings`

| Feature          | What it validates                                                |
| ---------------- | ---------------------------------------------------------------- |
| `spec.layerings` | Which layer keys are valid/required for deployments in a subtree |
| `spec.paths`     | What the directory structure must look like within that subtree  |

They are **complementary, not overlapping**. A platform can have layering without path
conventions (free folder structure) or path conventions without layering (structure enforced
but no deployment layer metadata). Most operators will declare both for the same subtrees.

---

## Proposed Model: `PathConventionModel`

### YAML Schema

```yaml
# configuration.yaml
spec:
  paths:
    - name: zone-deployment-tree
      scope: "zones/**"
      pattern: "zones/{zone}/customers/{tenant}/{env}"
      validate:                                          # YAML key; Python field: rules
        zone: spec.zones[*].name
        tenant: "customers/{tenant}/tenant.yaml"
        env: spec.environments[*].name

    - name: tenant-registry
      scope: "customers/**"
      pattern: "customers/{tenant}"
      validate:
        tenant: "customers/{tenant}/tenant.yaml"

    - name: landscape-registry
      scope: "landscape/**"
      pattern: "landscape/{landscape}"
      validate:
        landscape: "landscape/{landscape}/landscape.yaml"
```

### Field Reference

| Field      | Type                  | Required | Description                                                    |
| ---------- | --------------------- | -------- | -------------------------------------------------------------- |
| `name`     | `PlatformName`        | yes      | Unique convention name for diagnostics and policy filtering    |
| `scope`    | `str` (glob)          | yes      | Files matching this glob are candidates for this convention    |
| `pattern`  | `str` (path template) | yes      | Expected path structure with `{segment}` captures              |
| `validate` | `Dict[str, str]`      | no       | Per-segment validation rules (see Validation Rule Types below) |

### Design Constraints

1. **`{segment}` captures exactly one path part** — never matches `/`. Literal folder
   names in the pattern must match verbatim.
2. **Pattern is anchored at path start** — matched against relative path from `work_path`.
   Trailing path parts after the pattern (ring dirs, filenames) are ignored.
3. **Placeholder names link `pattern` → `validate`** — keys in `validate` must exactly
   match `{name}` placeholders in `pattern`. References in validate paths expand from
   the same captured dict.
4. **File existence rules are anchored at `work_path` root** — not relative to the file
   being validated.
5. **Multiple conventions may match one file** — all are evaluated independently. This is
   intentional for files at intersection points.
6. **No match ≠ violation** — if a file is in scope but doesn't match the pattern (e.g.,
   it's at a shallower depth), it's skipped. Only pattern-matched files get validated.

---

## Validation Rule Types

Two rule syntaxes in the `validate` dict:

### 1. Model field lookup (`spec.*`)

```yaml
validate:
  zone: spec.zones[*].name
  env: spec.environments[*].name
```

**Syntax:** `spec.{field}[*].{attribute}`

- Resolves against the loaded `ConfigurationModel`
- Requires `--deep` mode (configuration service must be available)
- If configuration service is unavailable → warn + skip (never fail)
- Checks that the captured segment value is a member of the resolved collection

**Resolution algorithm:**
1. Start at `configuration_model.spec`
2. Walk dot-separated path: `zones` → list
3. `[*]` — iterate over all items
4. `.name` — extract attribute from each item
5. Build membership set
6. Check `captured_value ∈ set`

### 2. File existence check (path template)

```yaml
validate:
  tenant: "customers/{tenant}/tenant.yaml"
```

**Syntax:** A path string containing `{segment}` placeholders

- Placeholders expanded from captured segment values
- Resulting path checked as `work_path / expanded_path`
- Works in both surface and deep validation (no configuration needed)
- Self-references (file checking its own existence) → warn, not error

**Resolution algorithm:**
1. Expand all `{placeholder}` references from captured values dict
2. Join with `work_path`: `Path(work_path) / expanded_path`
3. Check `exists()` on disk

---

## Pattern Matching Algorithm

Given a file at relative path `rel_path` and a convention with `pattern`:

```python
def match_pattern(rel_path: str, pattern: str) -> Optional[Dict[str, str]]:
    """Match a file path against a convention pattern.
    
    Returns captured segment values, or None if no match.
    """
    path_parts = Path(rel_path).parts
    pattern_parts = pattern.split("/")
    
    if len(path_parts) < len(pattern_parts):
        return None  # file is shallower than pattern — skip
    
    captures = {}
    for i, pat_part in enumerate(pattern_parts):
        actual_part = path_parts[i]
        if pat_part.startswith("{") and pat_part.endswith("}"):
            # Capture segment
            segment_name = pat_part[1:-1]
            captures[segment_name] = actual_part
        else:
            # Literal — must match exactly
            if actual_part != pat_part:
                return None  # literal mismatch
    
    return captures  # trailing parts beyond pattern are ignored
```

**Key behaviors:**

```
# Pattern: "zones/{zone}/customers/{tenant}/{env}"
# Path: "zones/europe/customers/contoso/prd/deploy.yaml"
#        ^^^^   ^^^^^^  ^^^^^^^^^  ^^^^^^^  ^^^  (pattern matches first 5 parts)
# Result: {zone: "europe", tenant: "contoso", env: "prd"}
# Trailing "deploy.yaml" is ignored ✓

# Pattern: "zones/{zone}/customers/{tenant}/{env}"
# Path: "zones/europe/shared/base.yaml"
#        ^^^^   ^^^^^^  ^^^^^^ ← literal "customers" not found
# Result: None (no match — file skipped, NOT a violation)

# Pattern: "customers/{tenant}"
# Path: "customers/contoso/tenant.yaml"
# Result: {tenant: "contoso"}
# Trailing "tenant.yaml" ignored ✓
```

---

## Evaluation Pipeline

### Full evaluation flow (per file)

```
1. Compute rel_path = file.relative_to(work_path)
2. For each convention in spec.paths:
   a. Scope check: fnmatch(rel_path, convention.scope)
      → No match: skip this convention
   b. Pattern match: match_pattern(rel_path, convention.pattern)
      → No match: skip (file in scope but at different depth)
   c. For each segment_name, rule in convention.validate:
      i.  Resolve rule type (spec.* or file path)
      ii. Evaluate rule against captured value
      iii. On failure: emit violation (field=segment_name, value=captured, rule=rule)
3. Collect all violations across all matching conventions
4. Return PolicyResult with violations list
```

### Integration with PolicyContext

```python
@dataclass
class PolicyContext:
    ...
    file_path: Optional[Path] = None  # NEW — the file being validated
```

The policy engine populates `file_path` before calling `evaluate()`. This is the
only `PolicyContext` change needed.

---

## Policy Type: `path_convention`

### Standard Usage (configuration-driven)

```yaml
# configuration.yaml
spec:
  paths:
    - name: zone-deployment-tree
      scope: "zones/**"
      pattern: "zones/{zone}/customers/{tenant}/{env}"
      validate:
        zone: spec.zones[*].name
        tenant: "customers/{tenant}/tenant.yaml"

  policies:
    - name: enforce-path-conventions
      type: path_convention
      phase: validate
      enforcement: deny
```

The policy reads `spec.paths` automatically. No `configuration` block needed.

### Filtered Usage (selective enforcement)

```yaml
policies:
  - name: strict-zone-check
    type: path_convention
    phase: validate
    enforcement: deny
    configuration:
      conventions: [zone-deployment-tree]

  - name: advisory-landscape-check
    type: path_convention
    phase: validate
    enforcement: warn
    configuration:
      conventions: [landscape-registry]
```

### Deploy-Repo Usage (inline convention)

For repositories without a configuration model (deploy repos), the convention is
declared inline on the policy:

```yaml
policies:
  - name: deploy-landscape-convention
    type: path_convention
    phase: validate
    enforcement: deny
    configuration:
      scope: "deploy/**"
      pattern: "deploy/{landscape}/{ring}"
      validate:
        landscape: "deploy/{landscape}/landscape.yaml"
```

When `configuration.scope` + `configuration.pattern` are present, the engine uses
them directly instead of reading from `spec.paths`.

---

## Pydantic Model

```python
class PathConventionModel(PlatformBaseModel):
    """A path convention rule for directory structure validation."""

    name: PlatformName = Field(description="Unique convention name for diagnostics")
    scope: str = Field(
        description=(
            "Glob pattern — only files whose relative path matches this "
            "scope are checked against the convention."
        )
    )
    pattern: str = Field(
        description=(
            "Path template with {segment} captures. Anchored at work_path root. "
            "Each {segment} captures exactly one path part (no '/'). "
            "Trailing path parts after the pattern are ignored."
        )
    )
    validate: Optional[Dict[str, str]] = Field(
        None,
        description=(
            "Per-segment validation rules. Keys must match {segment} names in pattern. "
            "Values: 'spec.field[*].attr' for model lookup, or path template for file existence."
        ),
    )

    @model_validator(mode="after")
    def validate_segments_match_pattern(self) -> "PathConventionModel":
        """Validate that all keys in 'validate' correspond to {segments} in pattern."""
        if not self.validate:
            return self
        import re
        pattern_segments = set(re.findall(r"\{(\w+)\}", self.pattern))
        for key in self.validate:
            if key not in pattern_segments:
                raise ValueError(
                    f"Validation key '{key}' does not correspond to a {{segment}} "
                    f"in pattern '{self.pattern}'. Available segments: {sorted(pattern_segments)}"
                )
        return self
```

**On `ConfigurationSpecModel`:**

```python
paths: Optional[List[PathConventionModel]] = Field(
    None,
    description="Declared directory structure conventions for path validation policy",
)
```

---

## Policy Implementation

```python
class PathConventionPolicy(BasePolicy):
    """Validates that files on disk follow declared path conventions."""

    def evaluate(self, context: PolicyContext) -> PolicyResult:
        violations: List[str] = []

        # Resolve convention source
        conventions = self._resolve_conventions(context)
        if not conventions:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no path conventions configured"},
            )

        # Must have file_path to validate
        if context.file_path is None or context.work_path is None:
            return PolicyResult(
                passed=True,
                policy_name=self.name,
                enforcement=self.enforcement,
                details={"skipped": "no file path in context"},
            )

        rel_path = context.file_path.relative_to(context.work_path).as_posix()

        for conv in conventions:
            # Step a: scope check
            if not fnmatch(rel_path, conv.scope):
                continue

            # Step b: pattern match
            captures = self._match_pattern(rel_path, conv.pattern)
            if captures is None:
                continue  # in scope but not at the right depth

            # Step c: validate each segment
            if conv.validate:
                for segment_name, rule in conv.validate.items():
                    value = captures.get(segment_name)
                    if value is None:
                        continue
                    violation = self._evaluate_rule(
                        segment_name, value, rule, captures, context
                    )
                    if violation:
                        violations.append(
                            f"convention '{conv.name}' — segment '{segment_name}' = '{value}': {violation}"
                        )

        return PolicyResult(
            passed=len(violations) == 0,
            policy_name=self.name,
            enforcement=self.enforcement,
            violations=violations,
        )
```

---

## Variant Folder Structures

Different folder layouts **require separate convention entries**:

```yaml
# Structure A: zones/{zone}/customers/{tenant}/{env}/deploy.yaml
# Structure B: zones/{zone}/{tenant}/{env}/deploy.yaml (no "customers" folder)

paths:
  - name: zone-tree-with-customers
    scope: "zones/**/customers/**"
    pattern: "zones/{zone}/customers/{tenant}/{env}"
    validate:
      zone: spec.zones[*].name
      tenant: "customers/{tenant}/tenant.yaml"

  - name: zone-tree-flat
    scope: "zones/**"
    pattern: "zones/{zone}/{tenant}/{env}"
    validate:
      zone: spec.zones[*].name
      tenant: "customers/{tenant}/tenant.yaml"
```

The more specific scope (`zones/**/customers/**`) should be listed first. A path
containing `/customers/` matches the first convention; paths without it fall through
to the flat convention. Both conventions are evaluated independently if both scopes
match — but the flat pattern won't match a path with a `customers/` literal in the
wrong position (pattern match fails, not a violation).

---

## Example Output

```
DENY  zones/atlantis/customers/contoso/dev/deploy.yaml
      convention 'zone-deployment-tree' — segment 'zone' = 'atlantis'
      not in spec.zones[*].name: [europe, nordics, us-east, us-west]

DENY  zones/europe/customers/unknown-co/prd/deploy.yaml
      convention 'zone-deployment-tree' — segment 'tenant' = 'unknown-co'
      customers/unknown-co/tenant.yaml does not exist

WARN  landscape/ghost/landscape.yaml
      convention 'landscape-registry' — segment 'landscape' = 'ghost'
      landscape/ghost/landscape.yaml does not exist
```

---

## Fleet-Wide Scan

```bash
# Validate all files against path conventions
strata validate run --pattern "**/*.yaml" --deep

# Target a specific subtree
strata validate run --pattern "zones/**/*.yaml" --deep
```

Exit code 3 when any `deny`-level violation exists. Warnings do not affect exit code.

---

## Integration with `spec.layerings`

Operators typically declare both features for the same subtree:

```yaml
spec:
  layerings:
    - name: zone-tenant-scheme
      scope: "zones/**"
      layers:
        - name: zone
          required: true
        - name: customer
          required: true
        - name: environment
          required: true
          default: dev

  paths:
    - name: zone-deployment-tree
      scope: "zones/**"
      pattern: "zones/{zone}/customers/{tenant}/{env}"
      validate:
        zone: spec.zones[*].name
        tenant: "customers/{tenant}/tenant.yaml"
        env: spec.environments[*].name
```

**Note:** Segment names in `spec.paths` (`tenant`, `env`) are independent from layer
names in `spec.layerings` (`customer`, `environment`). They can be the same or different
— the two features validate different aspects and don't cross-reference each other.

---

## Implementation Plan

### Step 1 — `PathConventionModel` on `ConfigurationSpecModel`

✅ `PathConventionModel` added with fields: `name`, `scope`, `pattern`, `rules` (alias `validate`)
✅ `validate_segments_match_pattern()` model validator added
✅ `paths: Optional[List[PathConventionModel]]` added to `ConfigurationSpecModel`
✅ Uniqueness validator for path convention names added

### Step 2 — Path matching utilities

✅ `src/strata/utils/path_convention.py` created with:
- `match_pattern(rel_path, pattern) -> Optional[Dict[str, str]]`
- `is_spec_rule(rule) -> bool`
- `resolve_spec_rule(rule, configuration_model) -> Optional[Set[str]]`
- `evaluate_file_rule(rule, captures, work_path) -> Optional[str]`
- `evaluate_conventions(rel_path, conventions, work_path, config_model) -> List[str]`

### Step 3 — Add `file_path` to `PolicyContext`

✅ `file_path: Optional[Path] = None` added to `PolicyContext` dataclass

### Step 4 — `PathConventionPolicy`

✅ `src/strata/validators/policies/path_convention_policy.py` created
✅ Inline convention source (deploy-repo) and spec.paths source both supported
✅ Convention filter via `configuration.conventions` list
✅ Registered in policy type dispatch

### Step 5 — Update policy type list

✅ `path_convention` added to type field description in `policy_model.py`

### Step 6 — Tests

✅ `tests/strata/validators/test_path_convention_policy.py` — 45 tests, all passing

### Step 7 — Documentation

⬜ Update `docs/config/configuration.md` — add `paths` section
⬜ Update policy types table with `path_convention`

---

## Open Questions

1. **Should `spec.*` rule syntax support nested dot paths beyond one level?**
   Current: `spec.zones[*].name`. Potential: `spec.providers[*].resources[*].name`.
   Recommendation: Start with single-level `[*].attr` — extend later if needed.

2. **Should there be an `ignore` field on conventions to exclude specific files?**
   E.g., exclude `.gitkeep` or `README.md` from pattern matching. Recommendation:
   Rely on scope globs (e.g., `zones/**/*.yaml` instead of `zones/**`) to filter.

3. **Should conventions support optional literal segments?**
   E.g., `zones/{zone}[/customers]/{tenant}/{env}`. Recommendation: No — require
   separate convention entries for variant structures. Simpler, more explicit.

4. **Performance at scale (1000+ files)?**
   Pattern matching is O(n × m) where n=files, m=conventions. Each match is string
   split + comparison — no regex. Should be fast enough for thousands of files.
   Profile in Phase 2 if needed.

---

## Consequences

- Directory naming drift is caught at validation time, before deployment
- New operators learn the expected structure from the convention declarations themselves
- Tenant onboarding (ADR 0040) can verify scaffold output against conventions
- CI gates can enforce structure consistency across the fleet
- No runtime performance impact — validation is a pre-deploy check only
