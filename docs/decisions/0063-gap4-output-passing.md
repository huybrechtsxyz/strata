# Gap 4 — Output passing between provisioners

- Status: completed
- Date: 2026-08-06
- Parent: [ADR 0063 — Team-owned Terraform module support](0063-vct-owned-terraform-module-support.md)

## Problem

With the two-root split (platform baseline + team module as separate provisioners), the
team module needs outputs from the baseline: `vnet_id`, `subnet_ids`, `cluster_id`, etc.

Today this requires hand-wiring `terraform_remote_state` data sources in the team module:

```hcl
data "terraform_remote_state" "baseline" {
  backend = "azurerm"
  config = {
    resource_group_name  = "rg-terraform-state"
    storage_account_name = "stterraformstate"
    container_name       = "tfstate"
    key                  = "baseline.tfstate"
  }
}

locals {
  vnet_id    = data.terraform_remote_state.baseline.outputs.vnet_id
  subnet_ids = data.terraform_remote_state.baseline.outputs.subnet_ids
}
```

This is fragile:
- Backend config is duplicated between strata's backend declaration and the data source.
- The team module must know the baseline's state key (tight coupling).
- Changes to the baseline's backend require updating every downstream module.
- No validation that expected outputs actually exist.

## Current state in strata

Strata **already has output passing between stages** via `ResolvedValues.stage_outputs`:

1. After each stage's `apply`, `collect_outputs()` runs `terraform output -json`.
2. Non-sensitive outputs are stored in `ResolvedValues.stage_outputs`.
3. Subsequent stages receive these as `TF_VAR_*` environment variables.

**What's missing:** This works at the **stage level** (deployment stages), but there is
no way to declare the dependency at the **workspace level** (provisioner → provisioner).
Stages reference provisioners, but the output flow is implicit — it depends on stage
execution order, not on an explicit contract.

### Current stage model

```yaml
# deployment.yaml — stages execute in order, outputs flow implicitly
spec:
  stages:
    - name: infrastructure
      provisioner: platform_baseline
      scope: all
      on_failure: stop

    - name: team_infra
      provisioner: team_module
      scope: all
      on_failure: stop
```

Because `infrastructure` runs before `team_infra`, its outputs are available as
`TF_VAR_*` env vars in the `team_infra` stage. But:
- This is undocumented and implicit.
- There's no validation that the team module's expected inputs match the baseline's outputs.
- The injection mechanism (`TF_VAR_*` env vars) requires the team module to declare
  variables with the exact same names as the baseline's outputs.

## Design

### Approach: Explicit `inputs_from` on provisioner

Add an `inputs_from` field to `WorkspaceIacModel` that declares which other provisioners'
outputs this provisioner consumes:

```python
# src/strata/models/workspace_model.py

class ProvisionerInputMappingModel(PlatformBaseModel):
    """Maps outputs from an upstream provisioner to inputs of this provisioner."""

    provisioner: PlatformName = Field(
        description="Name of the upstream provisioner whose outputs to consume"
    )
    mapping: Optional[Dict[str, str]] = Field(
        None,
        description=(
            "Optional output-to-input name mapping. Keys are upstream output names, "
            "values are downstream variable names. When omitted, outputs are passed "
            "through with their original names."
        ),
    )
    prefix: Optional[str] = Field(
        None,
        description=(
            "Optional prefix to add to all output names when injecting as inputs. "
            "Mutually exclusive with 'mapping'. E.g., prefix='baseline_' turns "
            "'vnet_id' into 'baseline_vnet_id'."
        ),
    )
    select: Optional[List[str]] = Field(
        None,
        description=(
            "Optional allowlist of output names to pass. When set, only these "
            "outputs are forwarded. When omitted, all non-sensitive outputs pass."
        ),
    )


# On WorkspaceIacModel:
inputs_from: Optional[List[ProvisionerInputMappingModel]] = Field(
    None,
    description=(
        "Declare dependencies on other provisioners' outputs. Outputs from the "
        "named provisioners are injected as Terraform variables into this provisioner."
    ),
)
```

### YAML surface

```yaml
spec:
  provisioners:
    - name: platform_baseline
      provisioner: terraform
      source:
        repository: iac-aks-core
        source_path: terraform
      backend:
        type: azurerm
        configuration:
          key: baseline.tfstate

    - name: team_module
      provisioner: terraform
      source:
        repository: team-infra
        source_path: terraform
      backend:
        type: azurerm
        configuration:
          key: team.tfstate
      inputs_from:
        - provisioner: platform_baseline
          mapping:
            vnet_id: platform_vnet_id
            subnet_ids: platform_subnet_ids
            cluster_id: aks_cluster_id
```

#### Prefix mode (simpler for pass-through)

```yaml
      inputs_from:
        - provisioner: platform_baseline
          prefix: baseline_
          # All outputs forwarded: vnet_id → baseline_vnet_id, etc.
```

#### Select mode (allowlist without renaming)

```yaml
      inputs_from:
        - provisioner: platform_baseline
          select:
            - vnet_id
            - subnet_ids
            - cluster_id
          # Only these 3 outputs forwarded, with original names
```

### Validation rules

1. **Referenced provisioner must exist:**
   ```python
   @model_validator(mode="after")  # on WorkspaceSpecModel
   def validate_inputs_from_references(self):
       provisioner_names = {p.name for p in self.provisioners}
       for prov in self.provisioners:
           if prov.inputs_from:
               for inp in prov.inputs_from:
                   if inp.provisioner not in provisioner_names:
                       raise ValueError(
                           f"Provisioner '{prov.name}': inputs_from references "
                           f"unknown provisioner '{inp.provisioner}'"
                       )
   ```

2. **No self-references:**
   ```python
   if inp.provisioner == prov.name:
       raise ValueError(f"Provisioner '{prov.name}' cannot reference itself in inputs_from")
   ```

3. **No circular dependencies:**
   ```python
   # Build dependency graph and check for cycles (topological sort)
   def validate_no_cycles(provisioners):
       graph = {p.name: set() for p in provisioners}
       for p in provisioners:
           if p.inputs_from:
               for inp in p.inputs_from:
                   graph[p.name].add(inp.provisioner)
       # Kahn's algorithm for cycle detection
       ...
   ```

4. **Mutual exclusivity of mapping/prefix:**
   ```python
   @model_validator(mode="after")
   def validate_mapping_prefix_exclusive(self):
       if self.mapping and self.prefix:
           raise ValueError("'mapping' and 'prefix' are mutually exclusive")
       return self
   ```

### Execution order

The deploy pipeline already executes stages in declared order. With `inputs_from`,
the build system should:

1. **At build time**: Validate the dependency graph (no cycles). Emit a
   `<stage>.inputs_from.json` file documenting the expected inputs for traceability.

2. **At deploy time**: The existing `stage_outputs` mechanism already handles injection.
   The `inputs_from` declaration adds:
   - **Name mapping**: Before injecting `TF_VAR_*` env vars, apply the mapping/prefix.
   - **Select filtering**: Only forward selected outputs.
   - **Validation**: After `collect_outputs()` on the upstream stage, verify that all
     mapped/selected keys are present in the output set.

### Injection mechanism

```python
def apply_input_mapping(
    upstream_outputs: Dict[str, Any],
    mapping_config: ProvisionerInputMappingModel,
) -> Dict[str, Any]:
    """Apply mapping/prefix/select to upstream outputs for downstream injection."""

    if mapping_config.select:
        # Filter to allowlist
        filtered = {k: v for k, v in upstream_outputs.items() if k in mapping_config.select}
        missing = set(mapping_config.select) - set(upstream_outputs.keys())
        if missing:
            raise ValueError(
                f"Upstream provisioner '{mapping_config.provisioner}' does not produce "
                f"expected outputs: {sorted(missing)}"
            )
    else:
        filtered = dict(upstream_outputs)

    if mapping_config.mapping:
        # Rename keys: upstream_name → downstream_name
        return {
            downstream_name: filtered[upstream_name]
            for upstream_name, downstream_name in mapping_config.mapping.items()
            if upstream_name in filtered
        }
    elif mapping_config.prefix:
        # Add prefix to all keys
        return {f"{mapping_config.prefix}{k}": v for k, v in filtered.items()}
    else:
        # Pass through unchanged
        return filtered
```

### Build-time artifact

Write `<provisioner>.inputs_from.auto.tfvars.json` into the build directory as a
**placeholder** documenting expected upstream variables:

```json
{
  "_comment": "These variables are populated at deploy-time from upstream provisioner outputs",
  "_upstream": "platform_baseline",
  "platform_vnet_id": null,
  "platform_subnet_ids": null,
  "aks_cluster_id": null
}
```

This serves as documentation and enables Gap 3 (input validation) to see these keys
as "supplied" so they don't trigger false "unsupplied required variable" warnings.

### Integration with Gap 3

When validating inputs:
- Keys declared in `inputs_from` mappings are treated as "supplied" even though their
  values are not known at build time.
- The set of expected inputs = environment YAML keys + inputs_from mapped keys.
- If Gap 3 validation is also checking the upstream module, it can verify that the
  upstream actually declares those outputs.

### Sensitive output handling

- By default, only non-sensitive outputs are forwarded (matching current `stage_outputs`
  behavior).
- Sensitive outputs from upstream are available in `stage_outputs_sensitive` but are NOT
  injected as env vars (security by default).
- To forward sensitive outputs, use the existing `allowed_secrets` mechanism on stages:

```yaml
spec:
  stages:
    - name: team_infra
      provisioner: team_module
      allowed_secrets: ["*"]  # ← opt-in to receive sensitive upstream outputs
```

### Manifest impact

The deployment manifest already records per-stage outputs. With `inputs_from`, add:

```json
{
  "stages": [{
    "name": "team_infra",
    "inputs_received": {
      "from": "platform_baseline",
      "keys": ["platform_vnet_id", "platform_subnet_ids", "aks_cluster_id"],
      "mapping_applied": true
    }
  }]
}
```

### Migration

- **Fully backward compatible**: `inputs_from` is `Optional`, defaults to `None`.
- Existing stage-ordered implicit output passing continues to work unchanged.
- Teams adopt `inputs_from` incrementally for explicit contracts.

### Test cases

1. Provisioner with `inputs_from` + mapping → outputs renamed correctly.
2. Provisioner with `inputs_from` + prefix → all outputs prefixed.
3. Provisioner with `inputs_from` + select → only selected outputs passed.
4. Missing expected output from upstream → error at deploy time.
5. Self-reference in `inputs_from` → validation error.
6. Circular dependency → validation error.
7. Reference to non-existent provisioner → validation error.
8. `mapping` + `prefix` both set → validation error.
9. Multiple `inputs_from` entries → outputs merged from all upstreams.
10. Name collision across multiple upstreams → error (duplicate key).

## Files to change

| File                                             | Change                                                     |
| ------------------------------------------------ | ---------------------------------------------------------- |
| `src/strata/models/workspace_model.py`           | Add `ProvisionerInputMappingModel` and `inputs_from` field |
| `src/strata/validators/workspace_validator.py`   | Validate references, cycles, mutual exclusivity            |
| `src/strata/builders/terraform_builder.py`       | Emit placeholder tfvars for inputs_from keys               |
| `src/strata/deployers/terraform_deployer.py`     | Apply mapping before injection                             |
| `src/strata/utils/resolved_values.py`            | Support mapped output injection                            |
| `src/strata/models/deployment_manifest_model.py` | Record inputs_received in stage model                      |
| `tests/strata/models/test_workspace_model.py`    | Validation tests                                           |
| `tests/strata/deployers/test_output_passing.py`  | Integration tests                                          |
| `docs/config/workspace.md`                       | Document `inputs_from`                                     |

## Alternatives considered

| Approach                              | Pros                         | Cons                                      |
| ------------------------------------- | ---------------------------- | ----------------------------------------- |
| `terraform_remote_state` (current)    | No strata changes            | Duplicates backend config, tight coupling |
| `inputs_from` on provisioner (chosen) | Explicit contract, validated | New schema surface                        |
| DAG-based provisioner graph           | Most elegant                 | Breaking change, complex migration        |
| Shared output file convention         | Simple                       | No validation, convention-only            |
