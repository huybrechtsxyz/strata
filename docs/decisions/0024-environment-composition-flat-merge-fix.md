# Environment composition — complete flat-merge and provenance tracking

- Status: completed
- Date: 2026-07-05
- Issue: [#170](https://github.com/huybrechtsxyz/strata/issues/170)

## Context and Problem Statement

strata allows deployments to compose environments from multiple files:

```yaml
# deploy-prd.yaml
spec:
  environments:
    - environments/base.yaml
    - environments/prd.yaml
```

`EnvironmentService.merge_envfiles()` merges these into a single `EnvironmentModel` before override application and value resolution. However, the current implementation has **three critical gaps**:

### Gap 1 — Incomplete section merging

`merge_envfiles()` only merges `variables`, `secrets`, and `features`. The remaining five `EnvironmentSpecModel` sections are **silently dropped** from every file after the first:

| Section      | Merged?               | Consequence                                                   |
| ------------ | --------------------- | ------------------------------------------------------------- |
| `variables`  | ✅ Last-wins by key    | Works correctly                                               |
| `secrets`    | ✅ Last-wins by key    | Works correctly                                               |
| `features`   | ✅ Last-list-wins      | Works correctly (wholesale replacement)                       |
| `overrides`  | ❌ Hardcoded to `None` | Environment overrides in non-first files are silently ignored |
| `properties` | ❌ Hardcoded to `None` | Properties in any file are silently ignored                   |
| `custom`     | ❌ Hardcoded to `None` | Custom data in any file is silently ignored                   |
| `lifecycle`  | ❌ Hardcoded to `None` | Lifecycle hooks in any file are silently ignored              |
| `audit`      | ❌ Hardcoded to `None` | Audit config in any file is silently ignored                  |

The merged `EnvironmentSpecModel` is constructed with explicit `None` for all five sections:

```python
spec = EnvironmentSpecModel(
    variables=variables,
    secrets=secrets,
    features=merged_features,
    lifecycle=None,       # ← dropped
    properties=None,      # ← dropped
    custom=None,          # ← dropped
    overrides=None,       # ← dropped
)
```

This means a `prd.yaml` file that defines resource count overrides or lifecycle hooks has **no effect** when composed with a `base.yaml`.

### Gap 2 — No provenance tracking

When a deployment uses multiple environment files, there is no way to determine which file contributed a given variable, secret, feature flag, or override. The `ResolvedValues` dataclass has `variable_notes`, `secret_notes`, and `feature_notes` dicts, but these record **resolution method** ("default: X", "generated"), not **source file origin**.

For a deployment using `[base.yaml, prd.yaml]` where both declare `db_host`, the user cannot tell whether the final value came from `base.yaml` or `prd.yaml`.

### Gap 3 — No `--trace` option on `values list`

The `strata values list` command shows resolved values with optional `--show-store` (store type and reference) and `--unresolved` flags. There is no way to see the merge order, which file each value originated from, or why a particular value won.

### Design decision: flat list, not hierarchy

We evaluated two approaches:

| Approach                     | Description                                                                        |
| ---------------------------- | ---------------------------------------------------------------------------------- |
| **Hierarchical inheritance** | Add `extends: parent.yaml` to environment schema; resolve parent chain recursively |
| **Flat list (fix gaps)**     | Keep existing `environments: [a.yaml, b.yaml]` list; fix the incomplete merge      |

**Chosen: Flat list.** Hierarchy adds complexity (circular dependency detection, diamond problem, hidden behavior, non-obvious resolution order) for minimal benefit. Real-world environments use 2–3 layers at most — a flat ordered list handles this cleanly without hidden parent chains. The deployment file already declares the exact merge order explicitly, which is easier to reason about and debug.

## Decision Drivers

- **Correctness**: all `EnvironmentSpecModel` sections must participate in composition.
- **Debuggability**: operators must be able to trace any resolved value back to its source file.
- **Backward compatibility**: existing single-file deployments and the current `[base, env]` pattern must work unchanged.
- **Simplicity**: no new YAML schema fields, no recursive resolution, no hidden parent chains.

## Decision Outcome

Fix `merge_envfiles()` to merge all sections, add source-file provenance to `ResolvedValues`, and expose a `--trace` flag on `strata values list`.

### Consequences

- Good: All environment spec sections work correctly in multi-file compositions.
- Good: Operators can debug value origin without reading source code.
- Good: No YAML schema changes — fully backward compatible.
- Good: Provenance data is available programmatically in JSON output.
- Bad: `ResolvedValues` grows three additional dicts (one per type for source tracking).
- Bad: `merge_envfiles()` becomes more complex (but the current version is incorrectly simple).

---

## Detailed Design

### 1. Complete section merging in `merge_envfiles()`

Extend the merge loop to handle all eight sections with clear, documented semantics:

| Section                  | Merge strategy                                                        | Rationale                                                                                  |
| ------------------------ | --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `variables`              | Last-wins by `key`                                                    | Existing behavior, correct                                                                 |
| `secrets`                | Last-wins by `key`                                                    | Existing behavior, correct                                                                 |
| `features`               | Last-wins by `key`                                                    | **Changed** from wholesale list replacement to per-key merge for consistency               |
| `overrides.resources`    | Last-wins by `resource` name                                          | Prd file should be able to override resource counts defined in base                        |
| `overrides.modules`      | Last-wins by composite key `(module, resource, namespace, slot_type)` | Matches existing uniqueness validator                                                      |
| `overrides.providers`    | Last-wins by `provider` name                                          | Same pattern as resources                                                                  |
| `overrides.properties`   | Shallow `dict.update()`                                               | Later file's properties overlay earlier ones; keys not present in later file are preserved |
| `overrides.includes`     | Append (deduplicate by source path)                                   | Includes are additive — each env file can contribute Terraform includes                    |
| `overrides.remotes`      | Last-wins by `remote` name                                            | Pin version per remote                                                                     |
| `overrides.output_files` | Append (deduplicate by output path)                                   | Output files are additive                                                                  |
| `properties`             | Shallow `dict.update()`                                               | Same as overrides.properties                                                               |
| `custom`                 | Shallow `dict.update()`                                               | Same as properties                                                                         |
| `lifecycle`              | Last-wins (wholesale)                                                 | Last file's lifecycle config takes precedence                                              |
| `audit`                  | Last-wins (wholesale)                                                 | Last file's audit config takes precedence                                                  |

#### Implementation sketch

```python
@classmethod
def merge_envfiles(cls, envfiles: List[str], work_path: Path) -> EnvironmentModel:
    merged_vars: Dict[str, VariableStoreModel] = {}
    merged_secrets: Dict[str, SecretStoreModel] = {}
    merged_features: Dict[str, FeatureStoreModel] = {}
    merged_lifecycle = None
    merged_properties: Dict[str, Any] = {}
    merged_custom: Dict[str, Any] = {}
    merged_audit = None
    meta = None

    # Override accumulators
    merged_resource_overrides: Dict[str, EnvironmentResourceOverrideModel] = {}
    merged_module_overrides: Dict[str, EnvironmentModuleOverrideModel] = {}
    merged_provider_overrides: Dict[str, EnvironmentProviderOverrideModel] = {}
    merged_remote_overrides: Dict[str, EnvironmentRemoteOverrideModel] = {}
    merged_override_properties: Dict[str, Any] = {}
    merged_includes: Dict[str, EnvironmentIncludeModel] = {}
    merged_output_files: Dict[str, OutputFileModel] = {}

    for envfile_path in envfiles:
        env_service = cls(str(work_path / envfile_path))
        is_valid, errors = env_service.validate()
        if not is_valid:
            raise ValueError(f"Invalid environment file: {envfile_path}\nErrors: {errors}")

        env_model = env_service.get_model()
        if not env_model or not env_model.spec:
            continue

        spec = env_model.spec
        if meta is None and env_model.meta:
            meta = env_model.meta

        # --- Variables: last-wins by key ---
        if spec.variables:
            for var in spec.variables:
                merged_vars[var.key] = var

        # --- Secrets: last-wins by key ---
        if spec.secrets:
            for secret in spec.secrets:
                merged_secrets[secret.key] = secret

        # --- Features: last-wins by key (changed from wholesale) ---
        if spec.features:
            for feat in spec.features:
                merged_features[feat.key] = feat

        # --- Properties: shallow merge ---
        if spec.properties:
            merged_properties.update(spec.properties)

        # --- Custom: shallow merge ---
        if spec.custom:
            merged_custom.update(spec.custom)

        # --- Lifecycle: last-wins ---
        if spec.lifecycle:
            merged_lifecycle = spec.lifecycle

        # --- Audit: last-wins ---
        if spec.audit:
            merged_audit = spec.audit

        # --- Overrides ---
        if spec.overrides:
            ovr = spec.overrides
            if ovr.resources:
                for res in ovr.resources:
                    merged_resource_overrides[str(res.resource)] = res
            if ovr.modules:
                for mod in ovr.modules:
                    key = f"{mod.module}:{mod.resource or ''}:{mod.namespace or ''}:{mod.slot_type or ''}"
                    merged_module_overrides[key] = mod
            if ovr.providers:
                for prov in ovr.providers:
                    merged_provider_overrides[str(prov.provider)] = prov
            if ovr.remotes:
                for rem in ovr.remotes:
                    merged_remote_overrides[str(rem.remote)] = rem
            if ovr.properties:
                merged_override_properties.update(ovr.properties)
            if ovr.includes:
                for inc in ovr.includes:
                    merged_includes[inc.source] = inc
            if ovr.output_files:
                for of in ovr.output_files:
                    merged_output_files[of.path] = of

    # Build merged OverridesModel (only if any override data exists)
    has_overrides = any([
        merged_resource_overrides, merged_module_overrides,
        merged_provider_overrides, merged_remote_overrides,
        merged_override_properties, merged_includes, merged_output_files,
    ])
    overrides = EnvironmentOverridesModel(
        resources=list(merged_resource_overrides.values()) or None,
        modules=list(merged_module_overrides.values()) or None,
        providers=list(merged_provider_overrides.values()) or None,
        remotes=list(merged_remote_overrides.values()) or None,
        properties=merged_override_properties or None,
        includes=list(merged_includes.values()) or None,
        output_files=list(merged_output_files.values()) or None,
    ) if has_overrides else None

    spec = EnvironmentSpecModel(
        variables=list(merged_vars.values()) or None,
        secrets=list(merged_secrets.values()) or None,
        features=list(merged_features.values()) or None,
        lifecycle=merged_lifecycle,
        properties=merged_properties or None,
        custom=merged_custom or None,
        overrides=overrides,
        audit=merged_audit,
    )
    # ...build and return EnvironmentModel
```

#### Behavior change: features

Current behavior replaces the entire features list with the last file's list. This is inconsistent with variables and secrets (which merge by key) and means a `prd.yaml` that enables one feature silently drops all features declared in `base.yaml`.

New behavior: merge features by `key`, same as variables. A `prd.yaml` can override a specific feature flag without affecting others declared in `base.yaml`. This is backward-compatible for the common case (single env file) and gives the expected behavior for multi-file (features from base are preserved unless explicitly overridden).

### 2. Provenance tracking in `ResolvedValues`

Add source-file information alongside the existing `*_notes` dicts:

```python
@dataclass
class ResolvedValues:
    # ... existing fields ...

    # Provenance: which environment file each declared value came from
    variable_sources: Dict[str, str] = field(default_factory=dict)
    secret_sources: Dict[str, str] = field(default_factory=dict)
    feature_sources: Dict[str, str] = field(default_factory=dict)
```

#### Where provenance is recorded

Provenance is captured during `merge_envfiles()`, not during value resolution. The merge method knows which file contributed each key — this information is lost today because merged models carry no memory of their source.

**Approach**: `merge_envfiles()` returns a new `MergedEnvironment` wrapper (or extends `EnvironmentModel`) that carries per-key source metadata:

```python
@dataclass
class MergeProvenance:
    """Tracks which environment file contributed each key during merge."""
    variable_sources: Dict[str, str] = field(default_factory=dict)   # key → file path
    secret_sources: Dict[str, str] = field(default_factory=dict)
    feature_sources: Dict[str, str] = field(default_factory=dict)
    override_sources: Dict[str, str] = field(default_factory=dict)  # composite key → file path
    merge_order: List[str] = field(default_factory=list)             # ordered file paths
```

`merge_envfiles()` populates this alongside the merge:

```python
provenance = MergeProvenance()
for envfile_path in envfiles:
    provenance.merge_order.append(envfile_path)
    # ... inside variable loop:
    provenance.variable_sources[var.key] = envfile_path
    # ... inside secret loop:
    provenance.secret_sources[secret.key] = envfile_path
    # ... etc.
```

The provenance is attached to the `EnvironmentService` (not the model — models must not carry runtime state) and propagated to `ValueController` → `ResolvedValues`.

**Alternative considered**: Storing provenance inside the Pydantic model. Rejected because models must be pure data with no runtime or filesystem state (per ADR-0003 layer rules and the design constraint that models load without a real filesystem).

### 3. `--trace` flag on `strata values list`

Add a `--trace` flag to the `values list` command:

```bash
strata values list -f deploy/deploy-prd.yaml --trace
```

#### Console output with `--trace`

```
Variables
─────────────────────────────────────────────────────────────
  Key              Value          Source
  db_host          prd.db.local   environments/prd.yaml
```

## Related

- [Environment Composition Guide](../guides/environment-composition.md) — user-facing
  walkthrough of composing environments from multiple files
  app_port         8080           environments/base.yaml
  log_level        warning        environments/prd.yaml  (overrides base.yaml)

Merge order: environments/base.yaml → environments/prd.yaml
```

When `--trace` is active:
- A "Source" column is added showing the file that contributed the final value.
- Keys that were overridden show "(overrides <file>)" annotation.
- A "Merge order" footer shows the composition sequence.
- For single-file deployments, the source column shows the single file path.

#### JSON output with `--trace`

Each row in the JSON output gains `source` and `overridden_from` fields:

```json
{
  "variables": [
    {
      "key": "db_host",
      "store": "constant",
      "store_ref": "prd.db.local",
      "display": "prd.db.local",
      "ok": true,
      "note": "",
      "source": "environments/prd.yaml",
      "overridden_from": ["environments/base.yaml"]
    }
  ],
  "merge_order": ["environments/base.yaml", "environments/prd.yaml"]
}
```

### 4. Implementation plan

#### Files to modify

| File                                            | Change                                                                                        |
| ----------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `services/environment_service.py`               | Extend `merge_envfiles()` to merge all sections; return `MergeProvenance` alongside the model |
| `models/environment_model.py`                   | No changes (schema is unchanged)                                                              |
| `utils/resolved_values.py`                      | Add `variable_sources`, `secret_sources`, `feature_sources` dicts                             |
| `utils/merge_provenance.py`                     | **New** — `MergeProvenance` dataclass                                                         |
| `controllers/value_controller.py`               | Accept and forward provenance to `ResolvedValues`                                             |
| `services/deployment_service.py`                | Store `MergeProvenance` from env merge; expose via getter                                     |
| `commands/cli_values.py`                        | Add `--trace` option                                                                          |
| `commands/deploy/list_values_deploy_command.py` | Add source column when `--trace`; include provenance in JSON output                           |

#### Files unchanged

| File                          | Why                                                         |
| ----------------------------- | ----------------------------------------------------------- |
| `models/environment_model.py` | No schema additions — provenance is runtime-only            |
| `deployers/*`                 | Deployers consume `ResolvedValues`; new fields are additive |
| `builders/*`                  | Build does not use value resolution                         |

### 5. Testing strategy

| Test                                   | Purpose                                                          |
| -------------------------------------- | ---------------------------------------------------------------- |
| `test_merge_envfiles_overrides`        | Verify resource/module/provider/remote overrides merge correctly |
| `test_merge_envfiles_properties`       | Verify properties/custom shallow-merge                           |
| `test_merge_envfiles_lifecycle_audit`  | Verify last-wins for lifecycle and audit                         |
| `test_merge_envfiles_features_by_key`  | Verify features now merge by key (not wholesale)                 |
| `test_merge_provenance_tracking`       | Verify each key records correct source file                      |
| `test_merge_provenance_override_chain` | Verify overridden_from tracks all previous sources for a key     |
| `test_values_list_trace_console`       | Verify `--trace` console output includes source column           |
| `test_values_list_trace_json`          | Verify `--trace` JSON includes source and merge_order            |
| `test_single_file_no_merge`            | Verify single-file deployment still works (no regression)        |
| `test_backward_compat_vars_secrets`    | Verify existing variable/secret merge behavior is unchanged      |

### 6. Migration and backward compatibility

- **No YAML schema changes**: existing environment files are valid without modification.
- **No CLI breaking changes**: `--trace` is additive; default output is unchanged.
- **Feature merge behavior change**: features switch from wholesale replacement to per-key merge. This is a minor semantic change but aligns with user expectations and matches variable/secret behavior. Any deployment relying on "last file replaces all features" would need to explicitly set unwanted features to `false` — a more correct and explicit pattern.
- **New `ResolvedValues` fields**: additive dataclass fields with `default_factory=dict` — fully backward-compatible.

## Links

- [ADR-0003: Layered Architecture](0003-layered-architecture.md) — layer dependency rules that constrain where provenance tracking can live
- [ADR-0005: Secret Resolution at Build Time](0005-secret-resolution-at-build-time.md) — how secrets flow through the system
