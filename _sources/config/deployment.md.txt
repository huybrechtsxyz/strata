# Deployment (`kind: deployment`)

The "container instance" (ADR-0011): executes a [`workspace`](workspace.md)'s provisioning recipe
against a specific [`environment`](environment.md), for a specific [`tenant`](tenant.md). The
workspace says *what* runs and in what order; the deployment says *where, with which values, and
with what runtime behavior*. `strata build run`/`strata deploy run` both take a deployment name.

## Schema

- `spec.workspace` — the [`workspace`](workspace.md) document this deployment instantiates
- `spec.environments[]` — [`environment`](environment.md) documents, merged left-to-right (later wins)
- `spec.tenant` — optional [`tenant`](tenant.md) reference; its defaults merge in first
- `spec.extends` — another deployment document (typically `partial: true`) whose spec is merged
  in before this one's (child wins; nested blocks deep-merge; stages merge by step name and
  `providers` and `assets` by slot; `environments` lists append base-first; other lists are replaced)
- `spec.partial` — `true` marks a base deployment as not independently deployable (a leaf picks it
  up via `extends`)
- `spec.providers[]` — rebind [`workspace`](workspace.md) provider slots for this deployment, each
  `{slot, provider}`: the workspace's `spec.providers[].slot` and the [`provider`](provider.md)
  document to use instead of the slot's default — see "Rebinding a provider" below
- `spec.assets[]` — rebind [`workspace`](workspace.md) asset slots for this deployment, each
  `{slot, asset}`: the workspace's `spec.assets[].slot` and the [`asset`](asset.md) document to use
  instead of the slot's default — see "Rebinding an asset" below
- `spec.locking` — deployment lock configuration
- `spec.layers` — deployment layer/scope segments
- `spec.stages[]` — runtime parameters per provisioning step, each:
  - `step` — the [`workspace`](workspace.md) `spec.execution[].name` these parameters apply to
    (never redefines what runs — only how)
  - `enabled` — bool, or a string expression (accepted but not yet evaluated)
  - `on_failure` — `stop`, `rollback`, or `continue`
  - `timeouts` — per-phase (`setup`/`check`/`plan`/`apply`/`destroy`) overrides
  - `health_checks[]` — post-stage `http`/`tcp` reachability probes
  - `secrets[]` — secret keys this stage needs injected
  - `namespace` / `helm_namespaces[]` — restrict a stage to specific namespaces
- `spec.configurations` / `spec.properties` / `spec.lifecycle` — passthrough/merge layers

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: deploy-base
  annotations:
    description: "Reusable base — extended per instance, not deployable alone"
spec:
  partial: true
  workspace: main
  # locking: ...
```

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: prd-deployment
  annotations:
    description: "Production instance — inherits structure from deploy-base"
  tags: ["production"]
spec:
  extends: deploy-base
  environments:
    - prd
  tenant: c0062
```

## Rebinding a provider

A [`workspace`](workspace.md) declares provider *slots* with a default document. A deployment
rebinds a slot to deploy the same workspace to another region or account — IaC code still
addresses `strata.providers.<slot>`; only the bound document (and its region, tags,
configuration) changes. Put the binding in a reusable `partial` base so every deployment in that
region just extends it:

```yaml
kind: deployment
meta:
  name: region-north
spec:
  partial: true
  extends: deploy-base
  providers:
    - slot: azure
      provider: azure-north     # another provider document of type azure
---
kind: deployment
meta:
  name: prd-north-deployment
spec:
  extends: region-north
  environments: [prd]
  tenant: c0062
```

- **Rebind only** — the bound document is used as-is; there is no field patching. Per-deployment
  values belong in [`environment`](environment.md) documents.
- Bindings merge by `slot` across `extends` (child wins), so a leaf can rebind another slot
  without dropping the base's.
- A tenant's `geographies` are checked against the **bound** provider's region, so a deployment
  can serve a tenant restricted to another geography only once it rebinds to a provider there.
- `strata validate` reports `provider_slot_unknown` (slot not declared by the workspace) and
  `provider_type_mismatch` (bound provider's `spec.type` differs from the slot's default) as
  errors, and `provider_binding_redundant` (binds the slot's own default) as a warning. A
  `partial` base is only judged once merged into the deployment that extends it, and the
  redundant warning is skipped for a document that itself uses `extends` (resetting a base's
  binding back to the default is legitimate).

## Rebinding an asset

The same rule applies to a workspace's [asset slots](workspace.md): a deployment binds a slot to
another [`asset`](asset.md) document. One workspace can then serve many customers, each with
files in their own repository, while the Terraform code keeps reading `local.strata_assets.<slot>`:

```yaml
kind: deployment
meta:
  name: prd-c0062-deployment
spec:
  extends: deploy-base
  environments: [prd]
  tenant: c0062
  assets:
    - slot: api
      asset: c0062-api       # an asset document that carries its own git `location`
```

A customer is therefore one small asset document plus this deployment; `strata.yaml` does not
grow. Put a binding shared by many deployments in a `partial` base, as for providers.

- **Rebind only**, and bindings merge by `slot` across `extends` (child wins), exactly as for
  providers.
- `strata validate` reports `asset_slot_unknown` (slot not declared by the workspace) as an error and
  `asset_binding_redundant` (binds the slot's own default) as a warning, with the same `partial`
  and `extends` handling as the provider codes. A bound document that does not exist is an
  `unknown_reference`.
- `strata deploy run` reports the bound document in `strata.assets.<slot>.asset`; it needs the
  directories `strata build run` produced for the same deployment.

## Notes

- `extends` composes *structure* (stages, workspace, provider bindings); `environments` composes
  *values* — a deployment can extend a base for its stage recipe while merging its own, different
  environment list.
- `versions`, `promotion`, `gates` are declared in the model (for CLI parity with real invocations)
  but have zero real production usage and no execution engine behind them yet.
- Run `strata values get prd-deployment KEY...` before `build run`/`deploy run` to see exactly
  what a deployment resolves.
