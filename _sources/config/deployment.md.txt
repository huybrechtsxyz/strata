# Deployment (`kind: deployment`)

The "container instance" (ADR-0011): executes a [`workspace`](workspace.md)'s provisioning recipe
against a specific [`environment`](environment.md), for a specific [`tenant`](tenant.md). The
workspace says *what* runs and in what order; the deployment says *where, with which values, and
with what runtime behavior*. `strata build run`/`strata deploy run` both take a deployment name.

## Schema

- `meta.params` — the [parameter sets](parameterset.md) this deployment reads, per role:
  `{spoke: spoke-acme-001}`, or a list per role merged in order. Any document of the `extends`
  chain may name sets; per role they merge root first, leaf last. `${param:<role>.<key>}` in the
  spec of any document of the chain reads them — see "Parameter sets" below
- `meta.name_rule` — optional: the name every deployable deployment built on this one must have,
  with `${param:}` tokens filled from its values (`spoke-${param:spoke.name}`); any other name is
  `name_rule_mismatch`
- `spec.workspace` — the [`workspace`](workspace.md) document this deployment instantiates
- `spec.environments[]` — [`environment`](environment.md) documents, merged left-to-right (later wins)
- `spec.tenant` — optional [`tenant`](tenant.md) reference; its defaults merge in first
- `spec.kube_context` — the context in the caller's kubeconfig that this deployment's Helm steps deploy to — see
  "Choosing the target" below
- `spec.docker_context` — the Docker context that this deployment's Compose steps deploy to — see "Choosing the
  target" below
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
- `spec.documentation` — render the handover document after each successful `deploy run` and store it with the
  audit record — see "Handover document" below
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
  tenant: contoso
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
  tenant: contoso
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
  name: prd-contoso-deployment
spec:
  extends: deploy-base
  environments: [prd]
  tenant: contoso
  assets:
    - slot: api
      asset: contoso-api       # an asset document that carries its own git `location`
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

## Tokens from the deployment

A workspace is shared by every deployment, so nothing written in it can say which one is running. Two tokens read
it from the deployment that is:

- `${deployment:name}`: this document's `meta.name`
- `${deployment:tenant}`: its `spec.tenant`, including one inherited through `extends`

The usual use is a Terraform state key, `key: ${deployment:name}.tfstate`, in the workspace's backend (see
"Terraform backend" in [workspace](workspace.md)). `strata validate` checks them for every deployment that
reaches the document: `${deployment:tenant}` on a deployment without a tenant is an error, as is any other key. A
`partial: true` base is judged only once a deployment extends it.

## Parameter sets

When many deployments differ only in their values (one per spoke, one per customer project), write the
deployment once as a base that reads `${param:<role>.<key>}`, and give each instance a
[parameter set](parameterset.md) and a short leaf:

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: spoke_deployment_base
  name_rule: spoke-${param:spoke.name}
spec:
  partial: true
  workspace: spoke
  tenant: ${param:spoke.tenant}
  environments:
    - solution-env
    - hub-${param:hub.name}-env
---
apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: spoke-acme-001
  params: { hub: hub-hub01, spoke: spoke-acme-001 }
spec:
  extends: spoke_deployment_base
```

The values are filled in when the solution loads, before anything runs, so they work in reference fields
(`tenant`, `environments`, `providers[].provider`) as well as free text. Each deployment is then checked as if it
had been written out in full; a problem is reported on the field you wrote, with the values that made it. Not in
`meta`, `spec.extends` or `spec.partial`. The rules are in [parameter sets](parameterset.md), a full example in
[How To: Write one deployment base for many spokes](../how-to/parameterised-deployment-bases.md).

## Choosing the target

Helm and Compose steps deploy to whatever cluster or Docker engine the caller's environment points at. Without a
named target, a wrong active context sends a production deployment somewhere else. Name it on the deployment:

```yaml
spec:
  extends: forge-base
  environments: [prd]
  kube_context: forge-prd      # Helm steps
  docker_context: hearth-prd   # Compose steps
```

Both fields work the same way: every call of that tool uses the named context, it is checked before the first step
runs, and the run stops before anything changes if it cannot be used. Without the field nothing changes at deploy,
and `strata validate` warns for a deployment with a step of that tool (`helm_kube_context_unset`,
`compose_docker_context_unset`). A partial base is not warned; the deployments extending it are, unless the base sets
the field. The value is a literal: a `${...}` token is a schema error. Strata never changes the caller's own
configuration: it does not set `KUBECONFIG` or `DOCKER_HOST`, write the kubeconfig, create a Docker context, or switch
the active context.

### Kubernetes (`kube_context`)

A Helm step deploys to a Kubernetes cluster through the kubeconfig the caller provides (`KUBECONFIG`, else
`~/.kube/config`). `kube_context` names a context in it:

- Every `helm` call of the deployment then runs against that context (`HELM_KUBECONTEXT`), whichever one is active:
  deploy, `--dry-run` preview, uninstall and destroy preview.
- Before the first step runs, strata checks that the kubeconfig defines the context. If it does not, the deploy stops
  with `helm_kube_context_missing`, listing the contexts it found. A `HELM_KUBECONTEXT` set to another context, or a
  `HELM_KUBEAPISERVER`, in the caller's environment stops it with `helm_kube_context_conflict`.
- Context names are local to a kubeconfig. Every machine that deploys (CI and laptops) must use the same context name
  for the cluster; `kubectl config rename-context` changes it.

### Docker (`docker_context`)

A Compose step runs `docker stack` against the engine Docker picks: `DOCKER_HOST`, else `DOCKER_CONTEXT`, else the
context chosen with `docker context use`. Create a context for each engine once
(`docker context create hearth-prd --docker host=ssh://deploy@hearth`) and name it:

- Every `docker` call of the deployment's Compose steps runs with `DOCKER_CONTEXT` set to it: `stack deploy`, the
  `--dry-run` preview (`stack config`), `stack rm` and the destroy preview (`stack services`).
- Before the first step runs, strata asks Docker (`docker context inspect`, which contacts no engine) whether the
  context exists. If not, the deploy stops with `compose_docker_context_missing`, listing the contexts Docker has. This
  also covers `--dry-run`, whose `docker stack config` never looks at the context.
- `DOCKER_HOST` silently overrides `DOCKER_CONTEXT`, so a set `DOCKER_HOST` stops the deploy with
  `compose_docker_context_conflict`, as does a `DOCKER_CONTEXT` naming another context. Replace a `DOCKER_HOST` in a
  workflow with a context created from the same host.
- `docker_context: default` is the local engine.

## Handover document

`spec.documentation` renders the handover document at the end of every successful `deploy run`: each step's outputs,
the secrets each step used with where to find them, and the images. It is the same document as
`strata deploy report --format markdown`, with that run's audit entry at the top. No secret value is ever in it:
every secret and every sensitive output shows as `********`.

```yaml
spec:
  documentation:
    sink: audit                     # the only sink: `_handover.md` next to the audit manifest
    template: docs/handover.md.j2   # optional; the built-in Markdown when unset
```

- `sink: audit` writes `_handover.md` next to `_manifest.json` and pushes it with every enabled `git` audit sink
  (Configuration `spec.audit.sinks[].git`). An integration sink sends events, not files, and does not carry it.
- `template` is a Jinja2 template relative to the solution root. It receives the data of
  `strata deploy report --output json`: `deployment`, `workspace`, `environments`, `steps`, `secrets`, `images`,
  `audit`, `generated_at`, and `MASK` (`********`). It runs sandboxed, and an unknown name fails.
- `strata validate` rejects it when the workspace has no `output_store` (`documentation_needs_output_store`), when
  no enabled git audit sink is configured (`documentation_needs_git_audit_sink`), or when the template is missing or
  uses a name the report does not have (`documentation_template_missing`, `documentation_template_invalid`).
- A render failure at the end of a deploy is a warning (`documentation_render_failed`), never a failed deploy:
  `strata deploy report` can render the document again.

The workflow, including handing a secret over with Password Pusher, is in
[deployment-handover.md](../how-to/deployment-handover.md).

## Notes

- `extends` composes *structure* (stages, workspace, provider bindings); `environments` composes
  *values* — a deployment can extend a base for its stage recipe while merging its own, different
  environment list.
- `versions`, `promotion`, `gates` are declared in the model (for CLI parity with real invocations)
  but have zero real production usage and no execution engine behind them yet.
- Run `strata values get prd-deployment KEY...` before `build run`/`deploy run` to see exactly
  what a deployment resolves.
