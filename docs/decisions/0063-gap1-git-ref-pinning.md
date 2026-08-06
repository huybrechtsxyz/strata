# Gap 1 — Git ref pinning on SourceModel

- Status: completed
- Date: 2026-08-06
- Parent: [ADR 0063 — Team-owned Terraform module support](0063-vct-owned-terraform-module-support.md)

## Problem

`SourceModel` supports version pinning for Helm charts (`chart_version`) but has no
equivalent for git-based sources. The git ref is defined once per remote in the
configuration's `spec.remotes[]`, and per-environment overrides exist via
`EnvironmentRemoteOverrideModel`. However, this operates at the **remote level** — all
provisioners referencing the same remote get the same ref.

When two provisioners in the same workspace reference the same remote but need different
versions (e.g., platform baseline pinned to `v1.4.0` while team module tracks `main`), the
current model cannot express this.

### Current resolution chain

```
RemoteModel.reference (configuration default)
  ↓ overridden by
EnvironmentRemoteOverrideModel.reference (per-environment)
  ↓ applies to ALL provisioners using that remote
```

### Desired resolution chain

```
RemoteModel.reference (configuration default)
  ↓ overridden by
EnvironmentRemoteOverrideModel.reference (per-environment)
  ↓ overridden by
SourceModel.reference (per-provisioner, highest priority)
```

## Design

### Schema change — `SourceModel`

Add a single optional field:

```python
# src/strata/models/common_models.py — SourceModel class

reference: Optional[Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]] = Field(
    None,
    description=(
        "Git ref override (branch, tag, or commit SHA) for this specific source. "
        "Takes precedence over the remote's default reference and any environment "
        "remote override. Only valid for git-based sources (repository + source_path)."
    ),
)
```

### Validation rules

1. **Mutual exclusivity with chart mode** — If `chart_repository` is set, `reference`
   must be `None`. Add to `validate_source_mode()`:

   ```python
   if self.reference is not None and has_chart:
       raise ValueError(
           "SourceModel.reference is only valid for git-based sources, "
           "not chart-based sources (use chart_version instead)."
       )
   ```

2. **Non-empty when present** — Handled by `StringConstraints(min_length=1)`.

3. **Format validation** — Accept any non-empty string. Git refs can be branches
   (`main`), tags (`v1.4.0`), short SHAs (`a1b2c3d`), or full SHAs. No format
   restriction beyond non-empty.

### YAML surface

```yaml
# workspace.yaml — provisioner with pinned ref
spec:
  provisioners:
    - name: platform_baseline
      provisioner: terraform
      source:
        repository: iac-aks-core
        source_path: terraform
        reference: v1.4.0          # ← pinned to specific tag

    - name: team_module
      provisioner: terraform
      source:
        repository: iac-aks-core
        source_path: terraform/team
        reference: main            # ← tracks latest
```

```yaml
# environment/prd/env.yaml — can still override at environment level
spec:
  overrides:
    remotes:
      - remote: iac-aks-core
        reference: v1.3.2        # ← this is now lowest priority for sources that set their own reference
```

### Resolution logic

Modify the source resolution in the build pipeline (where provisioner sources are
checked out / copied):

```python
def resolve_source_ref(
    source: SourceModel,
    remote: RemoteModel,
    env_override: Optional[EnvironmentRemoteOverrideModel],
) -> str:
    """Resolve the effective git ref for a source.

    Priority (highest first):
      1. source.reference (per-provisioner pin)
      2. env_override.reference (per-environment override)
      3. remote.reference (configuration default)
    """
    if source.reference:
        return source.reference
    if env_override and env_override.reference:
        return env_override.reference
    return remote.reference
```

### Build pipeline impact

**`TerraformBuilder._copy_provisioner_source()`** currently resolves the remote path
and copies files from the local repo checkout. The change:

1. Before copying, check if `source.reference` differs from the current checkout ref.
2. If different, perform a sparse checkout / archive extraction of the pinned ref into
   a temporary directory, then copy from there.
3. If the same (or `source.reference` is `None`), current behaviour unchanged.

**Implementation options for ref-specific checkout:**

| Approach                                            | Pros                          | Cons                                             |
| --------------------------------------------------- | ----------------------------- | ------------------------------------------------ |
| `git archive <ref> -- <path>`                       | Fast, no checkout needed      | Requires bare or fetch-enabled repo              |
| `git worktree add --detach <ref>`                   | Full workspace, tooling works | Disk-heavy for large repos                       |
| `git show <ref>:<path>` per file                    | No temp directory             | Slow for many files, no directory walk           |
| `git checkout <ref> -- <path>` in existing checkout | Simple                        | Mutates working tree (unsafe in parallel builds) |

**Recommended:** `git archive <ref> -- <source_path> | tar -x -C <temp_dir>`
- Does not mutate the working tree.
- Works with any ref (tag, branch, SHA).
- Fast for subtree extraction.
- Fallback: `git worktree add` for edge cases where `archive` fails.

### Manifest impact

`ManifestRepositoryModel` already records `ref` and `commit`. When `source.reference`
is set, the manifest should record the per-provisioner ref in the stage's `details`:

```json
{
  "stages": [{
    "name": "platform_baseline",
    "provisioner": "terraform",
    "details": {
      "source_ref_requested": "v1.4.0",
      "source_ref_resolved": "a1b2c3d4e5f6..."
    }
  }]
}
```

### Version lock interaction

The version-lock file (ADR 0011) pins remote refs at lock time. When
`source.reference` is set, the lock should record the **resolved commit SHA** for
that specific ref, not just the remote-level lock. This enables reproducible builds
even when `source.reference` is a mutable ref like `main`.

### Migration

- **Backward compatible**: `reference` is `Optional`, defaults to `None`.
- Existing workspaces continue to use remote-level refs unchanged.
- No migration required.

### Test cases

1. Source with `reference` set → resolved ref is the source's value.
2. Source with `reference` set + environment override → source wins.
3. Source without `reference` + environment override → env override wins.
4. Source without `reference` + no env override → remote default wins.
5. `reference` set on chart-based source → validation error.
6. `reference` set to empty string → validation error (min_length=1).
7. Build with mismatched refs across provisioners using same remote → each gets own ref.

## Files to change

| File                                              | Change                                                            |
| ------------------------------------------------- | ----------------------------------------------------------------- |
| `src/strata/models/common_models.py`              | Add `reference` field to `SourceModel`                            |
| `src/strata/builders/terraform_builder.py`        | Update `_copy_provisioner_source()` to respect `source.reference` |
| `src/strata/integrations/git.py`                  | Add `archive_path(ref, path)` method                              |
| `src/strata/models/deployment_manifest_model.py`  | Optionally record per-stage source ref                            |
| `tests/strata/models/test_source_model.py`        | Unit tests for validation                                         |
| `tests/strata/builders/test_terraform_builder.py` | Integration tests for ref-pinned copy                             |
| `docs/config/workspace.md`                        | Document new field                                                |
