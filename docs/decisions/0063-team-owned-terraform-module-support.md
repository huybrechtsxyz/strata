# Team-owned Terraform module support

- Status: proposed
- Date: 2026-08-06

## Remaining Work

- Not started — nothing in this ADR has been implemented yet.

## Context and Problem Statement

Platform teams provide a shared Terraform template (`iac_aks_core`). Product teams must
deliver their own infrastructure but do not own that repository. The agreed split is:

- **Platform baseline** — provides VNet, AKS, Key Vault, tags, naming.
- **Team module** — owned by the team in its own repository, consumed as a pinned module
  reference or as a second root module alongside the baseline.

Strata already handles this pattern well:

- `provisioners` is a list, each with its own `source` and `backend` — running a platform
  baseline and a team root module side by side, with separate state keys, works today.
- `SourceModel` separates repository identity from path (`repository` + `source_path`),
  keeping URLs out of deployment files.
- Output profiles (`format`, `emits[]`) control what reaches Terraform.

However, five gaps prevent this from being a fully supported, safe workflow.

## Gaps

### Gap 1 — Git sources cannot pin a version

`SourceModel` has `chart_version` for Helm, but nothing equivalent for git-based sources.
The git ref lives on the remote in `remotes.yaml`, meaning **one version per repository per
workspace**. It is impossible to have `dev` on `v1.5.0` and `prd` on `v1.4.0` from the same
repository without maintaining separate workspaces.

A `reference` field on `SourceModel` — mirroring `chart_version` — would allow per-source
ref pinning (branch, tag, or commit SHA) that overrides the workspace-level remote default.

### Gap 2 — Structured values must be smuggled through strings

Complex Terraform inputs are currently encoded as JSON inside a string value:

```yaml
variables:
  - key: aks_config
    value: '{"worker_pools": {"default": {"size": "Standard_D4s_v3"}}}'
```

There is no way to declare whether this should land in `.tfvars` as an **HCL object literal**
or a **quoted string** (requiring `jsondecode()` on the module side). Native structured values
in `variables` / `features` would remove the ambiguity and let strata emit proper HCL types.

### Gap 3 — No validation that declared inputs match the module's variables

A typo in a feature key (`enabld` instead of `enabled`) silently disappears — Terraform drops
undeclared `.tfvars` values, and the resource simply is not created. No error, no diff.

Since strata already resolves both sides at build time (the declared `variables`/`features`
and the module source path containing `variables.tf`), cross-checking declared inputs against
the module's variable declarations at `strata validate` time would catch this class of silent
failure before deploy.

### Gap 4 — Outputs do not flow between provisioners

With the two-root split, the team module needs the baseline's outputs (`vnet_id`,
`subnet_ids`, `cluster_id`). Today this requires hand-wiring `terraform_remote_state` data
sources. First-class output passing between provisioners in one workspace would make the split
a supported pattern rather than a convention.

### Gap 5 — Terraform outputs are not captured for the registry

The registration contract expects every deployment to write a JSON document describing what
it created. That data is exactly the module's Terraform outputs. Whether strata captures and
emits them, or each team writes a lifecycle script, is currently undecided. A built-in answer
makes the registry reliable by construction.

## Priority

| Priority | Gap                       | Rationale                                                     |
| -------- | ------------------------- | ------------------------------------------------------------- |
| 1        | Gap 3 — Input validation  | Prevents a class of silent failure; high ROI                  |
| 2        | Gap 1 — Git ref pinning   | Small schema addition with immediate release-management value |
| 3        | Gap 4 — Output passing    | Enables the two-root pattern without escape hatches           |
| 4        | Gap 5 — Output capture    | Makes registry population automatic                           |
| 5        | Gap 2 — Structured values | Quality-of-life; current workaround is functional             |

## Decision Drivers

- Silent deployment failures from undeclared variables are the highest-risk gap.
- Git ref pinning is a minimal schema change (one field on `SourceModel`) with outsized
  benefit for multi-environment release management.
- Output passing and capture are complementary — solving one makes the other easier.
- Structured values are a convenience improvement; the JSON-string workaround is viable.

## Considered Options

### Option A — Incremental schema extensions (recommended)

Address each gap as an isolated, backward-compatible schema addition:

1. **Gap 3**: Add `validate --check-inputs` phase that parses `variables.tf` from the
   resolved source and cross-checks against declared `variables`/`features` keys.
2. **Gap 1**: Add `reference: Optional[str]` to `SourceModel` (branch, tag, or SHA).
   When set, overrides the workspace-level remote ref for that source only.
3. **Gap 4**: Add `inputs_from: <provisioner_name>` to provisioner spec, instructing
   strata to run `terraform output -json` on the upstream provisioner and inject results
   as variables into the downstream one.
4. **Gap 5**: After successful apply, run `terraform output -json`, persist as
   `outputs.json` alongside the deployment manifest.
5. **Gap 2**: Extend the value model with a `type` discriminator (`string`, `number`,
   `bool`, `object`, `list`) so strata can emit native HCL literals.

### Option B — Monolithic provisioner-graph redesign

Redesign provisioners as a DAG with typed edges (output→input contracts). This solves
gaps 3–5 holistically but requires a breaking schema change and extensive migration work.

### Option C — External tooling convention

Document conventions for `terraform_remote_state`, input validation via `tflint`, and
output capture via lifecycle scripts. Zero code changes, but shifts burden to every team
and produces inconsistent implementations.

## Decision Outcome

**Option A — Incremental schema extensions.**

Each gap is addressed as a separate, backward-compatible change. Gaps are implemented in
priority order (3 → 1 → 4 → 5 → 2), each behind its own feature flag where appropriate.

### Consequences

**Positive:**

- No breaking changes; existing workspaces continue to work.
- Each gap can be shipped independently; no all-or-nothing delivery.
- Gap 3 (input validation) provides immediate safety improvement.
- Gap 1 (ref pinning) unblocks multi-environment version management without workspace
  duplication.

**Negative:**

- Incremental approach may accumulate schema surface area over time.
- Gap 4 (output passing) adds implicit coupling between provisioners that must be
  documented clearly.
- Gap 3 requires parsing `variables.tf` HCL, adding a Terraform HCL parser dependency.

**Neutral:**

- Gap 5 (output capture) aligns with the existing manifest artifact pattern.
- Gap 2 (structured values) is additive and can be deferred indefinitely.

## Implementation Notes

### Gap 3 — Input validation against variables.tf

- Parse `variables.tf` using `python-hcl2` (already available in the ecosystem).
- Extract declared variable names, types, defaults, and `nullable` attributes.
- At `strata validate` time (or `build run`), compare declared inputs against the parsed
  variable set:
  - **Error**: input key not found in `variables.tf` (typo detection).
  - **Warning**: required variable (no default) not supplied by any input.
  - **Info**: variable with default not overridden (informational, not blocking).
- Gated behind `validate --check-inputs` initially; promoted to default in a later release.

### Gap 1 — Git ref pinning on SourceModel

```yaml
# New field on SourceModel
source:
  repository: iac-aks-core
  source_path: terraform
  reference: v1.4.0          # ← new: overrides remote default ref
```

- `reference` is `Optional[str]`, validated as non-empty when present.
- Resolution order: `source.reference` → remote default ref → `HEAD`.
- Environment overrides can set different refs per environment via the existing
  `spec.overrides` mechanism.

### Gap 4 — Output passing between provisioners

```yaml
provisioners:
  - name: platform_baseline
    provisioner: terraform
    source:
      repository: iac-aks-core
      source_path: terraform
    backend: ...

  - name: team_module
    provisioner: terraform
    source:
      repository: team-infra
      source_path: terraform
    backend: ...
    inputs_from:
      - provisioner: platform_baseline
        # optional: map output names to input names
        mapping:
          vnet_id: platform_vnet_id
          subnet_ids: platform_subnet_ids
```

- Execution order inferred from `inputs_from` dependencies (topological sort).
- `terraform output -json` captured from upstream; mapped keys injected as
  `-var` arguments or written into a `.auto.tfvars.json` for the downstream.

### Gap 5 — Output capture

- After successful `terraform apply`, run `terraform output -json`.
- Persist result as `outputs.json` in the deployment artifact directory.
- Emit a structured `outputs` section in the deployment manifest.
- Registry consumers read the manifest; no separate capture step needed.

### Gap 2 — Structured values

```yaml
variables:
  - key: aks_config
    value:
      worker_pools:
        default:
          size: Standard_D4s_v3
    type: object   # ← new: tells strata to emit as HCL object
```

- `type` field discriminates output encoding: `string` (default, backward-compatible),
  `number`, `bool`, `object`, `list`.
- When `type: object` or `type: list`, the `value` field accepts a YAML mapping/sequence
  directly, and strata emits it as an HCL literal in `.tfvars`.

## Detailed designs

Each gap has a dedicated design document with schema changes, implementation details,
validation rules, test cases, and file-change lists:

- [Gap 1 — Git ref pinning](0063-gap1-git-ref-pinning.md)
- [Gap 2 — Structured values](0063-gap2-structured-values.md)
- [Gap 3 — Input validation](0063-gap3-input-validation.md)
- [Gap 4 — Output passing](0063-gap4-output-passing.md)
- [Gap 5 — Output capture](0063-gap5-output-capture.md)

## Related ADRs

- [ADR 0011 — Promotion strategies](0011-promotion-strategies-for-version-progression.md):
  version pinning (Gap 1) complements ring-based promotion.
- [ADR 0019 — Configurable Terraform build output](0019-configurable-terraform-build-output.md):
  output profiles interact with Gap 2 (structured values).
- [ADR 0042 — Deep validation](0042-deep-validation-layer-consistency.md):
  Gap 3 extends the deep validation concept to module input checking.
- [ADR 0023 — Pluggable provisioner framework](0023-pluggable-provisioner-framework.md):
  Gap 4 (output passing) builds on the provisioner abstraction.
