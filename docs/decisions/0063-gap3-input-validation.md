# Gap 3 — Input validation against variables.tf

- Status: completed
- Date: 2026-08-06
- Parent: [ADR 0063 — Team-owned Terraform module support](0063-vct-owned-terraform-module-support.md)

## Problem

A typo in a variable or feature key silently disappears. Terraform does not error on
undeclared values in `.tfvars` files — it drops them. The resource that depends on the
misspelled variable is simply not created. No error, no diff, no warning.

Example: `enabld_monitoring: true` instead of `enabled_monitoring: true` → the monitoring
resource's `count` remains `0`, the deployment succeeds, and the gap is only discovered
when someone notices monitoring is missing in production.

Since strata already resolves both sides at build time:
- **Declared inputs** — `variables` and `features` in environment YAML → emitted to tfvars.
- **Module source** — copied from the repository into the build directory → contains
  `variables.tf` with the canonical variable declarations.

Cross-checking one against the other is a natural extension of validation.

## Design

### Validation phase

This check runs as part of `strata build run` (after source copy, before tfvars write)
and optionally during `strata validate --check-inputs`:

```
strata validate deploy/deploy-prd.yaml --check-inputs
strata build run -f deploy/deploy-prd.yaml    # always runs check
```

### Architecture

```
┌─────────────────────────┐     ┌───────────────────────────────┐
│   Declared Inputs       │     │   Module variables.tf         │
│   (environment YAML)    │     │   (provisioner source)        │
├─────────────────────────┤     ├───────────────────────────────┤
│ variables:              │     │ variable "cluster_name" {     │
│   - key: cluster_name   │     │   type    = string            │
│   - key: enabld_mon     │◄──┐ │ }                             │
│ features:               │   │ │ variable "enabled_monitoring" │
│   - key: enable_ha      │   │ │   type    = bool              │
└─────────────────────────┘   │ │   default = false             │
                              │ │ }                             │
         ┌────────────────────┘ │ variable "enable_ha" {        │
         │  Cross-check         │   type    = bool              │
         │                      │   default = false             │
         │  ERROR: "enabld_mon" │ }                             │
         │  not found in        └───────────────────────────────┘
         │  variables.tf
         │
         │  WARN: "enabled_monitoring" (required=false)
         │  not supplied — will use module default
         └──────────────────────────────────────────────
```

### Parsing `variables.tf`

Use `python-hcl2` (already a dependency — `TerraformLoader` imports it):

```python
# src/strata/validators/terraform_input_validator.py

from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
import hcl2

@dataclass
class TerraformVariable:
    """Parsed variable declaration from variables.tf."""
    name: str
    type_expr: Optional[str]  # Raw HCL type expression (e.g. "string", "list(string)")
    has_default: bool          # True if `default = ...` is present
    default_value: Any         # The default value (None if no default)
    nullable: bool             # True if `nullable = true` (Terraform default is true)
    description: Optional[str]
    sensitive: bool            # True if `sensitive = true`
    validation_rules: int      # Count of validation{} blocks


def parse_variables_tf(source_path: Path) -> Dict[str, TerraformVariable]:
    """Parse all variable declarations from .tf files in a directory.

    Scans all *.tf files in source_path for `variable` blocks.
    Returns a dict keyed by variable name.
    """
    variables: Dict[str, TerraformVariable] = {}

    for tf_file in source_path.glob("*.tf"):
        with open(tf_file, "r", encoding="utf-8") as f:
            parsed = hcl2.load(f)

        for var_block in parsed.get("variable", []):
            # hcl2 parses variable blocks as [{name: {type: ..., default: ...}}]
            for var_name, var_body in var_block.items():
                variables[var_name] = TerraformVariable(
                    name=var_name,
                    type_expr=_extract_type(var_body),
                    has_default="default" in var_body,
                    default_value=var_body.get("default"),
                    nullable=var_body.get("nullable", True),
                    description=var_body.get("description"),
                    sensitive=var_body.get("sensitive", False),
                    validation_rules=len(var_body.get("validation", [])),
                )

    return variables
```

### Cross-check logic

```python
@dataclass
class InputCheckResult:
    """Result of cross-checking declared inputs against module variables."""
    errors: List[str]     # Undeclared input keys (definite typos)
    warnings: List[str]   # Required variables not supplied
    info: List[str]       # Optional variables not overridden (informational)


def check_inputs(
    declared_keys: Set[str],
    module_variables: Dict[str, TerraformVariable],
    excluded_keys: Optional[Set[str]] = None,
) -> InputCheckResult:
    """Cross-check declared input keys against module variable declarations.

    Args:
        declared_keys: Variable/feature keys from environment YAML.
        module_variables: Parsed variables from variables.tf.
        excluded_keys: Keys to skip (e.g. strata-injected variables like
                       'strata_context', 'strata_sensitive').
    """
    excluded = excluded_keys or set()
    result = InputCheckResult(errors=[], warnings=[], info=[])

    # 1. Find undeclared inputs (typo detection)
    module_var_names = set(module_variables.keys())
    for key in declared_keys:
        if key in excluded:
            continue
        if key not in module_var_names:
            # Fuzzy match for better error messages
            suggestion = _find_closest(key, module_var_names)
            msg = f"Input '{key}' is not declared in variables.tf"
            if suggestion:
                msg += f" (did you mean '{suggestion}'?)"
            result.errors.append(msg)

    # 2. Find required variables not supplied
    for var_name, var in module_variables.items():
        if var_name in excluded:
            continue
        if not var.has_default and var_name not in declared_keys:
            result.warnings.append(
                f"Required variable '{var_name}' (no default) is not supplied by any input"
            )

    # 3. Info: optional variables not overridden
    for var_name, var in module_variables.items():
        if var_name in excluded:
            continue
        if var.has_default and var_name not in declared_keys:
            result.info.append(
                f"Variable '{var_name}' has default={var.default_value!r} and is not overridden"
            )

    return result
```

### Fuzzy matching for suggestions

```python
from difflib import get_close_matches

def _find_closest(key: str, candidates: Set[str], cutoff: float = 0.6) -> Optional[str]:
    """Find the closest matching variable name for typo suggestions."""
    matches = get_close_matches(key, list(candidates), n=1, cutoff=cutoff)
    return matches[0] if matches else None
```

### Excluded keys

Strata injects several variables automatically that don't appear in the user's
`variables.tf` (or do appear but shouldn't trigger warnings):

```python
STRATA_INJECTED_KEYS = frozenset({
    "strata_context",          # STRATA_CONTEXT JSON envelope
    "strata_sensitive",        # STRATA_SENSITIVE JSON envelope
    # Add others as discovered
})
```

Additionally, the builder should only check variables that are **emitted to tfvars**.
Variables from non-constant stores (resolved at deploy-time) are injected as
`TF_VAR_*` env vars and may not appear in tfvars files — but they should still be
checked against `variables.tf`.

### Integration points

#### During `strata build run`

```python
# In TerraformBuilder.build(), after _copy_provisioner_source() succeeds:

for provisioner in terraform_provisioners:
    source_dir = self._get_provisioner_build_dir(provisioner, build_path)
    module_vars = parse_variables_tf(source_dir)
    declared = self._collect_declared_keys(platform_model, provisioner)
    result = check_inputs(declared, module_vars, excluded_keys=STRATA_INJECTED_KEYS)

    for error in result.errors:
        self._errors.append(f"[{provisioner.name}] {error}")
    for warning in result.warnings:
        self._warnings.append(f"[{provisioner.name}] {warning}")

    if result.errors:
        return False  # Block the build
```

#### During `strata validate --check-inputs`

Requires the source to be available (either checked out locally, or via the solution
repo map). If the source is not available, emit a warning and skip:

```
Warning: Cannot validate inputs for provisioner 'platform_baseline' —
source repository 'iac-aks-core' is not available locally.
Run 'strata repo sync' to fetch sources.
```

### Error output format

```
ERROR [platform_baseline] Input 'enabld_monitoring' is not declared in variables.tf (did you mean 'enabled_monitoring'?)
ERROR [platform_baseline] Input 'obsolete_flag' is not declared in variables.tf
WARN  [platform_baseline] Required variable 'subscription_id' (no default) is not supplied by any input
WARN  [team_module] Required variable 'resource_group_name' (no default) is not supplied by any input
INFO  [platform_baseline] Variable 'tags' has default={} and is not overridden
```

### JSON output (for `--output json`)

```json
{
  "success": false,
  "data": {
    "input_validation": {
      "platform_baseline": {
        "errors": [
          {"key": "enabld_monitoring", "message": "not declared", "suggestion": "enabled_monitoring"}
        ],
        "warnings": [
          {"key": "subscription_id", "message": "required variable not supplied"}
        ],
        "info": [
          {"key": "tags", "message": "has default={}, not overridden"}
        ]
      }
    }
  },
  "errors": ["Input 'enabld_monitoring' is not declared in variables.tf (did you mean 'enabled_monitoring'?)"],
  "messages": []
}
```

### Severity configuration

Allow teams to control enforcement via the existing policy mechanism 

```yaml
# configuration.yaml
spec:
  policies:
    - name: tf_input_check
      type: terraform_input_validation
      enforcement: deny       # deny = block on errors, warn = report only
      configuration:
        undeclared: error
        unsupplied: warn
```

### Edge cases

1. **Modules with `variable {}` in subdirectories** — Only scan the root of
   `source_path`. Terraform only reads variables from the root module directory.

2. **Generated variables** — Some modules use `for_each` or dynamic blocks that
   create variable-like behavior. These don't appear in `variables.tf` and won't
   cause false positives (we only check declared inputs → module variables, not the
   reverse).

3. **`terraform.tfvars` vs `.auto.tfvars`** — Both are checked by Terraform. Strata
   emits `.auto.tfvars.json`. The check covers all keys that strata would emit,
   regardless of target file.

4. **Variables supplied via `TF_VAR_*` env vars** — These come from
   `stage_outputs` (Gap 4) and deploy-time resolution. They should still be checked.
   The check uses the full set of declared keys (from environment YAML + stage outputs
   from `inputs_from` declarations).

5. **Partial modules (modules that use `optional()`)** — Terraform 1.3+ supports
   optional object attributes. The check only validates top-level variable names,
   not nested attribute completeness.

### Performance considerations

- `parse_variables_tf()` scans only `*.tf` files in the source root (typically 5-15
  files). Parsing is fast (< 100ms for typical modules).
- The check adds negligible time to the build pipeline.
- No network calls required — sources are already local after `_copy_provisioner_source()`.

### Rollout plan

1. **Phase 1**: Implement as `--check-inputs` flag on `strata validate` and always-on
   during `strata build run`. Undeclared inputs are errors; unsupplied required
   variables are warnings.
2. **Phase 2**: Add fuzzy-match suggestions and `input_validation` policy control.
3. **Phase 3**: Integrate with Gap 2 (structured values) for type-level cross-checking.

## Files to change

| File                                                        | Change                                             |
| ----------------------------------------------------------- | -------------------------------------------------- |
| `src/strata/validators/terraform_input_validator.py`        | New module: parsing + cross-check logic            |
| `src/strata/builders/terraform_builder.py`                  | Call input validation after source copy            |
| `src/strata/commands/cli_validate.py`                       | Add `--check-inputs` flag                          |
| `src/strata/models/workspace_model.py`                      | Optional `input_validation` on `WorkspaceIacModel` |
| `tests/strata/validators/test_terraform_input_validator.py` | Unit tests                                         |
| `tests/strata/builders/test_terraform_builder_inputs.py`    | Integration tests                                  |
| `docs/guides/input-validation.md`                           | User-facing documentation                          |

## Dependencies

- `python-hcl2` — already imported by `TerraformLoader` (no new dependency).
- `difflib` — stdlib (fuzzy matching).
