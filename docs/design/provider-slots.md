# Provider Slots — Design

- Status: current
- Last updated: 2026-10-19

## Overview

A [`workspace`](../config/workspace.md)'s providers are **named slots**, each with a default
[`provider`](../config/provider.md) document. A [`deployment`](../config/deployment.md) can rebind
a slot to another provider document of the same type, so the same workspace deploys to another
region or account — westeurope vs northeurope — without its IaC code changing. Rebinding is the
only operation (no field patching), and reuse across deployments goes through `extends`/`partial`.

Before this, a workspace bound provider documents by name with no way to change them per
deployment: a workspace pointing at a westeurope provider could not be deployed for a tenant
restricted to another geography at all (validation failed), and the only workaround was one
workspace per region, duplicating the whole recipe.

## Current Design

### Workspace: slots with a default

```yaml
kind: workspace
spec:
  providers:
    - slot: azure              # stable key — IaC code addresses strata.providers.azure
      provider: azure-main     # default Provider document
    - azure-shared             # bare name = slot and document share the name
```

`WorkspaceProviderModel {slot, provider}` (`workspace_model.py`).

- `slot` is unique within the workspace; two slots may point at the same document.
- A bare string is shorthand for `{slot: <name>, provider: <name>}` (`mode="before"` coercion, the
  `VersionPinModel` precedent), so a workspace that never rebinds anything is unchanged and keeps
  its `strata.providers.<name>` keys.
- The shorthand is invisible to `model_json_schema()`, so the model rewrites its own schema to
  `anyOf: [<name>, <object>]` through the shared `scalar_shorthand_schema()` helper
  (`common_models.py`, also used by `VersionPinModel`); otherwise the exported
  `.strata/schemas/` would flag every bare-string entry in the editor. A test validates the whole
  shipped example against its own exported schemas.
- The `provider` field carries `References(PlatformKind.PROVIDER)`; the reference walker recurses
  into nested models, so existence checks and `strata graph` edges need no special handling (the
  reported location is `spec.providers.N.provider`).

### Deployment: rebind slots

```yaml
kind: deployment
meta: { name: region-north }
spec:
  partial: true
  extends: deploy-base
  providers:
    - slot: azure
      provider: azure-north    # another document of the same type
---
kind: deployment
meta: { name: prd-north-deployment }
spec:
  extends: region-north
  environments: [prd]
  tenant: c0062
```

`DeploymentProviderBindingModel {slot, provider}` / `DeploymentSpecModel.providers`
(`deployment_model.py`). Slots are unique per deployment. There is no bare-string shorthand: a
binding only means something when it differs from the default. A binding names a slot, so the
workspace stays customer- and region-agnostic; the deployment — already the per-region/per-tenant
composition root — carries the choice.

### Merging across `extends`

`merge_deployment_specs()` (`deployment_service.py`) merges lists of named items by their identity
key, through one table:

```python
_LIST_MERGE_KEYS = {"stages": "step", "providers": "slot", "assets": "slot"}
```

Base items first; a child item with the same key deep-merges over it (child wins, position kept);
new keys append. The other rules are unchanged: nested blocks deep-merge per leaf key,
`environments` append base-first without duplicates, and any other list is replaced wholesale.
Because bindings merge by slot, a leaf can rebind a different slot without dropping its base's
bindings.

### Effective binding

`effective_providers(workspace, deployment) -> {slot: provider document name}`
(`services/workspace_service.py`) is the workspace's defaults overlaid by the deployment's
bindings, through a private `_overlay_bindings()` that `effective_assets()` shares; a binding for an undeclared slot is ignored there (validation reports it). Every reader
of the workspace's providers goes through it:

| Consumer                                                       | Behaviour                                                                                                                   |
| -------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `build_resolved_workspace_graph()` (`build_controller.py`)     | `graph.providers` keyed by **slot**, holding the bound document                                                             |
| `_strata_providers()` (`terraform_projection.py`)              | `strata.providers.<slot>`, plus `provider: <document name>` so IaC and logs see which document is bound                     |
| `_check_deployment_tenant_geography()` (`semantic_checks.py`)  | checks the **bound** provider's region against the tenant's `geographies` (once per document, even when two slots share it) |
| `_documents_reachable_from_workspace()` (`semantic_checks.py`) | scans the **bound** provider for value tokens (once per document); a default the deployment rebinds away is not reachable   |

`build_run()` and `deploy_run()` already receive the deployment with `extends` and tenant defaults
folded in, so rebinding reaches the generated `strata.auto.tfvars.json` with no further wiring.

### Validation

`_check_deployment_provider_bindings()` (`semantic_checks.py`), per deployment:

| Code                         | Severity | When                                                             |
| ---------------------------- | -------- | ---------------------------------------------------------------- |
| `provider_slot_unknown`      | error    | the deployment binds a slot its workspace does not declare       |
| `provider_type_mismatch`     | error    | the bound provider's `spec.type` differs from the slot's default |
| `provider_binding_redundant` | warning  | the binding names the slot's own default, so it has no effect    |

- A `partial` document is skipped: its `workspace` may be overridden by the deployment that
  extends it, so slots are judged on the merged result.
- The redundant warning is skipped when the *raw* document uses `extends`: after the merge, a
  child resetting a base's binding back to the default is indistinguishable from a pointless one,
  and resetting is legitimate. A redundant binding inside a partial base is therefore never
  reported.
- The location is `spec.providers` (not an index) because list positions no longer match the
  source file after a merge; the slot name is in the message. When the raw document uses
  `extends`, the message also says the binding may be inherited, since the finding is attributed
  to the deployment's own file.
- Existence of the bound document is the generic reference check's job; the region is checked
  against the provider registry by `_check_providers()` for every provider document, bound or not.

### Dogfood

`config/` ships a second region: `providers/azure-north.yaml`, the workspace's `azure` slot, a
`region-north` partial base (an intermediate link: it extends `deploy-base` and adds the binding)
and `prd-north-deployment` extending it. `strata build run` renders `strata.providers.azure` with
`azure-main`/`westeurope` for `prd-deployment` and `azure-north`/`northeurope` for
`prd-north-deployment`; a regression test pins both.

### Out of scope

- Patching fields of a bound provider, and environment-level overrides (`environment.spec.overrides`).
  Per-deployment values belong in environments.
- Rebinding other workspace content (resources, modules) — tracked in
  [workspace-overrides.md](../work/workspace-overrides.md), which reuses this pattern. Assets
  already do: see [asset-management.md](asset-management.md).

## Related Decisions

- [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md) — workspace/provisioning
  decoupling; removed v1's per-stage provider binding.
- [ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md) — identity is
  `(kind, meta.name)`, never a file.
- [ADR-0024](../decisions/0024-tenant-defaults-merge.md) — the deployment merge axes (`extends`,
  tenant defaults) this builds on.
- [tenant-zone-policy.md](../work/tenant-zone-policy.md) — the geography check that now evaluates
  the bound provider.
- [asset-management.md](asset-management.md) — the same slot-and-rebind rule for assets; its
  `_check_slot_bindings()` now holds the validation rules both share, with the provider type check
  as an `on_rebound` callback.

## History

- **v1 had this, unused.** v1's ADR-0036 added `environment.spec.overrides.providers`: swap a
  provider *file* per environment and patch its properties. It shipped but none of the tracked
  consumers (`haven`, `cfg-int`/`iac-int`/`env-int-deployment`) ever used it. v2 rebuilt the
  capability only once there was a real need (the same workspace in another Azure region), and
  not in v1's shape.
- **Deployment, not environment.** v2 already splits the two: environments supply *values*,
  deployments compose *structure* (`extends`' own description). Choosing which document fills a
  slot is structure — the same kind of choice as `workspace:` or `version:`. Environments also
  stack (tenant first, then the deployment's), so a structural rebind could have come from any
  layer and needed its own precedence rule; on the deployment the existing `extends` rule
  (child wins) is the precedence.
- **A slot layer is unavoidable.** v1 kept the provider's name and swapped the file. v2 identity
  is `(kind, meta.name)`, so two documents cannot share a name and a rebind necessarily points at
  a different name — which would change every key derived from the document name
  (`strata.providers.<name>` in IaC code). A separate, stable slot name restores that stability;
  a bare string keeps existing workspaces and keys intact.
- **Rebind only.** Patching fields on top of a bound document (v1's `configuration` half) would
  leave the provider document no longer stating the truth, and a different account differs
  wholesale (auth, integration), not field by field.
- **Reuse through `extends`.** The objection to binding on the deployment — every deployment in a
  region repeats it — is answered by a `partial` base carrying the binding once.
- **`extends` did not replace whole top-level fields.** The original description claimed it did;
  reading `merge_deployment_specs()` showed dicts deep-merge, `stages` merge by `step`,
  `environments` append, and only other lists replace. The stale `extends` field description was
  corrected. A new keyed list would still have been replaced wholesale, so keyed lists merge by
  their key — the convention of Kustomize (`patchMergeKey`), Compose (`services` by name) and
  strata's own environments (variables/secrets/features by `key`) — via one table rather than a
  special case per field.
- **Same type only.** Rebinding `azure` to an `aws` provider would feed Azure IaC an AWS
  provider; v1 never checked this. v2 requires the replacement's `spec.type` to match.
- **Validation follows the merged result.** Partial bases are judged only once merged, and the
  redundant-binding warning stays quiet for documents that use `extends`, because a reset to the
  default is a legitimate override that the merge makes indistinguishable from a no-op.
- **A coercing shorthand needs its own schema.** A `mode="before"` bare-string shorthand is invisible to the exported JSON Schema, so the editor flagged it even though `strata validate` accepted it. Found while checking the shipped example against its own exported schemas: the provider slot had the gap, and so did the long-standing `VersionPinModel` shorthand in `versions/prd.yaml`. One shared helper fixes both, and a test over the whole example keeps it fixed.
- **Two slots on one document report once.** Slots are allowed to share a document, so the geography check and the value-token scan deduplicate by document; otherwise the same finding appeared once per slot with no way to tell them apart.
