# Gap 2 — Structured values (native HCL types)

- Status: completed
- Date: 2026-08-06
- Parent: [ADR 0063 — Team-owned Terraform module support](0063-vct-owned-terraform-module-support.md)

## Problem

Complex Terraform inputs must currently be encoded as JSON strings:

```yaml
variables:
  - key: aks_config
    store: constant
    value: '{"worker_pools": {"default": {"size": "Standard_D4s_v3"}}}'
```

This has three problems:

1. **Ambiguity** — It is unclear whether this should be emitted as a quoted JSON string
   (module declares `variable "aks_config" { type = string }` + uses `jsondecode()`) or
   as an HCL object literal (module declares `variable "aks_config" { type = any }`).
2. **Authoring friction** — JSON-in-YAML-string is error-prone and hard to read.
3. **Validation gap** — Since the value is opaque, strata cannot type-check it against
   the module's declared variable type.

## Design

### Schema change — `VariableStoreModel`

Add an optional `type` field that declares the intended HCL type for emission:

```python
# src/strata/models/store_models.py — VariableStoreModel class

class VariableValueType(str, Enum):
    """Declared HCL type for variable emission."""
    STRING = "string"       # Default — emit as JSON string in tfvars
    NUMBER = "number"       # Emit as bare numeric literal
    BOOL = "bool"           # Emit as true/false (no quotes)
    OBJECT = "object"       # Emit as HCL object literal
    LIST = "list"           # Emit as HCL list literal
    MAP = "map"             # Emit as HCL map literal (string keys)

# On VariableStoreModel:
type: Optional[VariableValueType] = Field(
    None,
    description=(
        "Declared HCL type for Terraform emission. When set, strata emits the value "
        "as a native HCL literal in .auto.tfvars.json. When omitted (default), values "
        "are emitted as strings for backward compatibility."
    ),
)
```

### YAML surface

```yaml
# Simple scalar — backward compatible (no type field = string)
variables:
  - key: db_name
    store: constant
    value: "mydb"

# Explicit number
variables:
  - key: replica_count
    store: constant
    value: 3
    type: number

# Boolean
variables:
  - key: enable_ha
    store: constant
    value: true
    type: bool

# Native object — YAML mapping in value field
variables:
  - key: aks_config
    store: constant
    type: object
    value:
      worker_pools:
        default:
          size: Standard_D4s_v3
          min_count: 3
          max_count: 10
      network_profile:
        network_plugin: azure
        service_cidr: "10.0.0.0/16"

# Native list
variables:
  - key: allowed_ips
    store: constant
    type: list
    value:
      - "10.0.0.0/8"
      - "172.16.0.0/12"
```

### Emission rules

The `TerraformBuilder._build_terraform_vars()` method currently writes
`variables.auto.tfvars.json`. The emission changes based on `type`:

| `type` value     | `value` YAML type | Emitted JSON type | Terraform interprets as |
| ---------------- | ----------------- | ----------------- | ----------------------- |
| `None` (default) | any               | `"string"`        | `string`                |
| `string`         | scalar            | `"string"`        | `string`                |
| `number`         | int/float         | `42` / `3.14`     | `number`                |
| `bool`           | bool              | `true`/`false`    | `bool`                  |
| `object`         | mapping           | `{...}`           | `object`/`any`          |
| `list`           | sequence          | `[...]`           | `list`/`tuple`          |
| `map`            | mapping           | `{...}`           | `map(string)`           |

Since tfvars are emitted as `.auto.tfvars.json` (JSON format), the distinction is
straightforward: JSON natively supports all these types. The `type` field serves as
a contract declaration rather than a format conversion:

- **Without `type`**: `value` is always serialized as a JSON string (current behavior).
- **With `type: object|list|map`**: `value` is serialized as the raw JSON structure.
- **With `type: number|bool`**: `value` is serialized as the native JSON type.

### Validation rules

1. **Type-value consistency**:
   ```python
   @model_validator(mode="after")
   def validate_type_value_consistency(self) -> "VariableStoreModel":
       if self.type is None:
           return self  # backward compatible — no validation
       if self.type == VariableValueType.OBJECT and not isinstance(self.value, dict):
           raise ValueError(f"Variable '{self.key}': type=object requires a mapping value")
       if self.type == VariableValueType.LIST and not isinstance(self.value, list):
           raise ValueError(f"Variable '{self.key}': type=list requires a sequence value")
       if self.type == VariableValueType.MAP and not isinstance(self.value, dict):
           raise ValueError(f"Variable '{self.key}': type=map requires a mapping value")
       if self.type == VariableValueType.NUMBER and not isinstance(self.value, (int, float)):
           raise ValueError(f"Variable '{self.key}': type=number requires a numeric value")
       if self.type == VariableValueType.BOOL and not isinstance(self.value, bool):
           raise ValueError(f"Variable '{self.key}': type=bool requires a boolean value")
       return self
   ```

2. **Store compatibility** — Only `constant` store benefits from `type` (other stores
   resolve values at deploy-time). Warn (not error) when `type` is set on non-constant
   stores:
   ```
   Warning: Variable 'aks_config' declares type=object but uses store='vault'.
   Type enforcement only applies to constant-store values; resolved values will be
   emitted as-is regardless of declared type.
   ```

3. **Feature flags** — Feature flags are always boolean. No `type` field on
   `FeatureStoreModel` (they already emit `true`/`false`).

### Builder changes

In `TerraformBuilder._build_terraform_vars()`:

```python
def _emit_variable_value(self, var: VariableStoreModel) -> Any:
    """Convert a variable's value to the JSON-serializable form for tfvars emission."""
    if var.type is None:
        # Backward compatible: everything is a string
        return str(var.value) if var.value is not None else None

    # Typed emission: value passes through as-is (already the correct Python type
    # after Pydantic validation ensures type-value consistency)
    return var.value
```

### Integration with Gap 3 (input validation)

When Gap 3 is implemented, the `type` field enables richer cross-checking:

- `type: object` on a variable → module's `variables.tf` should declare `type = any`
  or `type = object({...})`, not `type = string`.
- `type: number` → module should declare `type = number`, not `type = string`.

This catches mismatches where the module expects a string (and uses `jsondecode()`)
but strata emits a raw object.

### Migration

- **Fully backward compatible**: `type` is `Optional`, defaults to `None`.
- Existing variables without `type` continue to emit as strings (current behavior).
- Teams adopt `type` incrementally as they update variable declarations.

### Test cases

1. Variable with `type: object` + dict value → emits as JSON object in tfvars.
2. Variable with `type: list` + list value → emits as JSON array.
3. Variable with `type: number` + int value → emits as bare number.
4. Variable with `type: bool` + True → emits as `true`.
5. Variable without `type` + dict value → emits as JSON string (stringified).
6. Variable with `type: object` + string value → validation error.
7. Variable with `type: number` + string value → validation error.
8. Variable with `type` on non-constant store → warning (not error).
9. Existing YAML files without `type` → unchanged behavior (regression test).

## Files to change

| File                                              | Change                                          |
| ------------------------------------------------- | ----------------------------------------------- |
| `src/strata/models/store_models.py`               | Add `VariableValueType` enum and `type` field   |
| `src/strata/builders/terraform_builder.py`        | Update `_build_terraform_vars()` emission logic |
| `src/strata/validators/platform_validator.py`     | Warn on type + non-constant store               |
| `tests/strata/models/test_store_models.py`        | Validation unit tests                           |
| `tests/strata/builders/test_terraform_builder.py` | Emission integration tests                      |
| `docs/config/environment.md`                      | Document `type` field                           |

## Interaction with output profiles

The `OutputProfileModel` (`format: strata|custom|script|none`) and `OutputFileModel`
already support `type: object|map|list|flat|script` for custom file definitions. The
new `VariableValueType` is consistent with this existing pattern — it extends the same
concept to individual variable declarations rather than whole output files.
