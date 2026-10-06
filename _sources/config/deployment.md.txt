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
  in before this one's (child wins; stages merge by step name; `environments` lists append base-first)
- `spec.partial` — `true` marks a base deployment as not independently deployable (a leaf picks it
  up via `extends`)
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

## Notes

- `extends` composes *structure* (stages, workspace); `environments` composes *values* — a
  deployment can extend a base for its stage recipe while merging its own, different environment list.
- `versions`, `promotion`, `gates` are declared in the model (for CLI parity with real invocations)
  but have zero real production usage and no execution engine behind them yet.
- Run `strata values get prd-deployment KEY...` before `build run`/`deploy run` to see exactly
  what a deployment resolves.
