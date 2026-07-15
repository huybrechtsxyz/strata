# GitOps Controller Integration

- Status: completed
- Date: 2026-07-15

## Context and Problem Statement

In fleet-scale deployments (ADR 0038) strata manages the configuration source of truth:
environment variable/secret layers, version-lock files, and promotion records. The actual
application workloads on Kubernetes are deployed by a GitOps controller (e.g., ArgoCD
ApplicationSets, Flux Kustomizations) that watches the configuration repository and reconciles
the cluster state.

This relationship is currently **implicit and unverified**:

- Strata knows what it intends to be deployed (version-lock files, resolved env vars).
- The GitOps controller knows what is actually running (reconciliation status, sync state).
- There is no contract between them — the controller reads git changes and acts; strata has
  no way to confirm reconciliation succeeded or detect drift between intended and actual state.

Additionally, the controller needs structured input to generate Applications across hundreds
of tenants. Today this is typically achieved by a controller-specific generator (e.g., ArgoCD
ApplicationSet git generator reading path patterns) with no awareness of strata's layer model,
version-lock files, or tenant metadata.

### Why not let the controller handle versions natively?

Both ArgoCD and Flux have built-in mechanisms for tracking new Helm chart versions:

- **ArgoCD** — an Application can set `spec.source.targetRevision: *` or use a semver
  constraint. ArgoCD's Helm chart tracker polls the HelmRepository and automatically
  detects new versions. Combined with auto-sync, the cluster reconciles to the latest
  matching version without any external trigger.

- **Flux** — a `HelmRelease` with `spec.chart.spec.version: ">=2.0.0 <3.0.0"` plus a
  `HelmRepository` source will reconcile to the latest chart version matching that range
  on its poll interval. Flux's `ImagePolicy` can do the same for container images.

**Why this is insufficient for fleet-scale operations:**

1. **No promotion control.** The controller treats every tenant identically. When a new
   chart version appears, *all* Applications using that chart update simultaneously.
   There is no concept of waves, rings, canary deployments, or progressive rollout
   across tenant cohorts. Strata's promotion system (ADR 0011) is specifically designed
   to gate version progression: dev → staging → production, with health verification
   between each ring.

2. **No per-tenant version pinning.** In practice, different tenants may need different
   versions — a customer on a maintenance contract stays on v2.3.x while others move
   to v2.4.x. The controller's native semver constraint applies uniformly; there is no
   per-Application override without duplicating the constraint in every Application spec.
   Strata's version-lock files express per-deployment version pins.

3. **No coordinated rollout.** Even if version constraints differ, the controller acts
   independently per Application. There is no atomic "deploy v2.4.1 to these 50 tenants
   in wave 1, then these 200 in wave 2 after health is confirmed." Strata's fleet
   operations (ADR 0037) provide this orchestration layer.

4. **No audit trail.** The controller tracks *what is running* but not *who decided it
   should run* or *why this version was promoted*. The version-lock file committed by
   strata's promotion workflow is the auditable decision record; the controller is the
   executor, not the decision-maker.

5. **No cross-concern coordination.** A version bump often requires corresponding variable
   changes (new feature flags, API endpoint changes, schema migrations). The controller
   sees chart versions in isolation; strata resolves the full deployment context (versions
   + variables + secrets + provider config) as a single atomic unit.

**In summary:** ArgoCD and Flux are excellent at *reconciling declared state to a cluster*.
They are not designed to *decide what that state should be across a heterogeneous fleet*.
That is strata's role. The integration contract is:

```
strata decides WHAT (versions, variables, per-tenant pins, wave ordering)
    → writes resolved state to git
        → controller reconciles git state TO the cluster
            → strata verifies reconciliation succeeded
```

Without this integration, the alternative is either:
- Let the controller auto-update everything (no control, no promotion gates), or
- Manually edit hundreds of Application specs per version bump (the N×M×K problem).

## Related Work

- **ADR 0038 — Multi-Tenant Fleet Management Patterns**: identifies this as Gap 4 (Medium).
- **ADR 0037 — Fleet Operations and Mass Wave Deployment**: fleet deploy execution; GitOps
  health verification is the post-deploy confirmation step.
- **ADR 0011 — Promotion Strategies**: version-lock files are the artefact the GitOps
  controller should consume for chart/image version resolution.
- **ADR 0018 — Deployment Audit Traceability**: reconciliation status from the controller
  should be recorded in the audit log as a deployment confirmation event.
- **ADR 0039 — Deployment Templates**: partial deployment files enable clean separation of
  infrastructure stages from application sync stages.

---

## Design Overview

### Key insight: no new commands needed

ArgoCD and Flux integrate as **provisioner types** within the existing stage/build/deploy
lifecycle. No new CLI verbs are introduced:

| Existing command       | What it does for terraform    | What it does for argocd/flux                         |
| ---------------------- | ----------------------------- | ---------------------------------------------------- |
| `strata build run`     | Generates Terraform artifacts | Renders Jinja2 template → controller input file      |
| `strata deploy run`    | Runs `terraform apply`        | Commits rendered output to git (triggers controller) |
| `strata deploy health` | Checks infra state            | Queries controller for reconciliation status         |

### Deployment separation with partial files (ADR 0039)

Partial deployment files enable clean separation of infrastructure provisioning from
application sync:

```yaml
# deploy-infra-base.yaml — provision infrastructure (partial)
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: infra-base
spec:
  partial: true
  workspace:
    name: workspace_platform
    file: "@iac/workspaces/workspace-platform.yaml"
  stages:
    - name: provision
      provisioner: platform_iac
      scope: infrastructure
      on_failure: stop
```

```yaml
# deploy-apps-base.yaml — sync applications (partial)
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: apps-base
spec:
  partial: true
  workspace:
    name: workspace_apps
    file: "@iac/workspaces/workspace-apps.yaml"
  stages:
    - name: sync
      provisioner: argocd
      backend:
        integration: argocd-prod
        remote: helm-config
      scope: applications
      on_failure: stop
```

```yaml
# deploy-contoso-dev.yaml — leaf, deployable
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: deploy-contoso-dev
spec:
  extends: "@config/templates/deploy-apps-base.yaml"
  tenant: contoso
  layers:
    zone: europe-west
    customer: contoso
    environment: dev
  stages:
    - name: sync
      namespace: contoso-dev       # ← scopes to this namespace
  environments:
    - "@config/zones/europe-west/env.yaml"
    - "@config/customers/contoso/dev/env.yaml"
```

#### Namespace as the unit of sync

The `namespace` field on a stage references one of `workspace.spec.namespaces[].name`.
The namespace YAML declares which modules belong to it — this becomes the set of
applications the controller manages for that namespace:

```yaml
# namespaces/contoso-dev.yaml
apiVersion: strata.huybrechts.xyz/v1
kind: namespace
meta:
  name: contoso-dev
spec:
  modules:
    - name: integrator-core
    - name: integrator-worker
```

The sync provisioner filters the platform artifact to the scoped namespace and its
modules. The template receives:
- `namespace` — the single `PlatformNamespaceModel` (name + modules)
- `modules[]` — filtered to only modules declared in that namespace
- All other context (layers, variables, tenant, etc.) remains available

If the stage omits `namespace`, all namespaces are included and the template is
responsible for iterating (via `{% for ns in namespaces %}`).

This gives teams the flexibility to:
- **Single deployment** — one file with both terraform and argocd stages
- **Split deployments** — `deploy-infra.yaml` (terraform only) + `deploy-apps.yaml` (argocd only)
- **Shared bases** — partial templates with common stage structure, thin leaves per tenant

---

### Integration model — `spec.integrations`

ArgoCD and Flux are configured via the existing integration framework in the configuration
model. The `sync` capability identifies them as reconciliation controllers:

```yaml
# configuration.yaml
spec:
  integrations:
    - name: argocd
      type: argocd
      capabilities: [sync]
      endpoints:
        address: https://argocd.platform.example.com
      authentication:
        type: token
        secret: argocd_api_token
      properties:
        template: sync/argocd-appset-entry.json.j2
        output_file: sync/appset-params.json
        project: default
        app_label_selector: "strata.io/managed=true"

    - name: flux
      type: flux
      capabilities: [sync]
      properties:
        template: sync/flux-kustomization.yaml.j2
        output_file: sync/kustomizations.yaml
        namespace: flux-system
        label_selector: "strata.io/managed=true"
      authentication:
        type: kubeconfig
        secret: flux_kubeconfig
```

### Provisioner lifecycle

When a stage has `provisioner: argocd` (or `flux`), the provisioner class implements:

| Phase      | Action                                                                                     |
| ---------- | ------------------------------------------------------------------------------------------ |
| **Build**  | Read platform artifact → filter by stage `namespace` → render Jinja2 → write `output_file` |
| **Deploy** | Commit rendered output to config repo (via `backend.remote`) → optionally trigger sync     |
| **Health** | Query controller API for reconciliation status → return `ReconciliationResult`             |

```python
class ArgoCDProvisioner(BaseProvisioner):
    """ArgoCD provisioner — renders sync artifacts and verifies reconciliation."""

    def build(self, context: BuildContext) -> bool:
        """Render Jinja2 template with resolved deployment context."""
        ...

    def deploy(self, context: DeployContext) -> bool:
        """Commit rendered artifacts; optionally trigger ArgoCD sync."""
        ...

    def health(self, context: HealthContext) -> ReconciliationResult:
        """Query ArgoCD API for sync/health status."""
        ...
```

---

## Controller Input Requirements

### What each controller needs

| Concern                  | ArgoCD (ApplicationSet)                                | Flux (Kustomization / HelmRelease)               |
| ------------------------ | ------------------------------------------------------ | ------------------------------------------------ |
| **Generator input**      | JSON list of parameter sets (list generator)           | Kustomization/HelmRelease CRs per app            |
| **Namespace**            | `spec.template.spec.destination.namespace`             | `spec.targetNamespace` on the CR                 |
| **Chart/image versions** | `spec.source.targetRevision` / Helm `values.image.tag` | `spec.chart.version` / `ImagePolicy`             |
| **Variables**            | `spec.source.helm.parameters[]` or inline `values`     | `spec.postBuild.substituteFrom` or `spec.values` |
| **Cluster targeting**    | `spec.destination.server` (URL or name)                | `spec.kubeConfig.secretRef`                      |
| **Health status API**    | REST: `GET /api/v1/applications/{name}`                | K8s CR: `.status.conditions[]`                   |
| **Auth model**           | Bearer token (API key or OIDC)                         | kubeconfig to management cluster                 |

### Platform artifact as template context

The platform artifact (`platform.json`) that `strata build run` already produces contains
everything the sync provisioner needs. No separate "universal manifest" is required — the
provisioner serializes the platform artifact to a dict and passes it as the Jinja2 context.

The artifact already contains:

| Data            | Platform artifact path                | Source                                                |
| --------------- | ------------------------------------- | ----------------------------------------------------- |
| Deployment name | `meta.name`                           | deployment YAML                                       |
| Layers          | `spec.deployment`                     | `spec.layers` resolved against configuration layering |
| Artifact path   | `spec.artifact_path`                  | computed from layer values                            |
| Tenant          | `spec.tenant.code`, `.name`, `.zones` | tenant YAML (ADR 0012)                                |
| Stages          | `spec.stages[]`                       | deployment YAML                                       |
| Topologies      | `spec.topologies[]`                   | workspace YAML                                        |
| Providers       | `spec.providers[]`                    | provider YAML (has `.properties.region`, `.zone`)     |
| Namespaces      | `spec.namespaces[]`                   | namespace YAML (has `.modules[]`)                     |
| Modules         | `spec.modules[]`                      | module YAML (has `.source` with chart/image refs)     |
| Variables       | `spec.variables[]`                    | layer stack merge (resolved, non-secret)              |
| Secrets         | `spec.secrets[]`                      | **excluded from template context**                    |
| Resources       | `spec.resources[]`                    | resource YAML                                         |

#### Convenience fields to add to `PlatformSpecModel`

To make the platform artifact more ergonomic as a Jinja2 context, we add computed
convenience fields. These are derived at build time and require no new input schema:

```python
# Additions to PlatformSpecModel

# Deployment name promoted from meta.name — templates don't need meta access
name: Optional[str] = Field(
    None, description="Deployment name (promoted from meta.name)")

# Labels and annotations promoted from workspace — useful for controller label selectors
labels: Optional[Dict[str, str]] = Field(
    None, description="Workspace labels (promoted from workspace.labels)")

annotations: Optional[Dict[str, Any]] = Field(
    None, description="Workspace annotations (promoted from workspace.annotations)")

# Flat dict of layer key→value (currently stored as spec.deployment)
# Alias for clarity in templates: {{ layers.zone }} vs {{ deployment.zone }}
layers: Optional[Dict[str, str]] = Field(
    None, description="Layer key-value pairs (alias for deployment)")

# Flat dict of module name→version from version-lock resolution
chart_versions: Optional[Dict[str, str]] = Field(
    None, description="Module/chart name → resolved version")

# Flat dict of module name→full image reference (registry:tag@digest)
image_versions: Optional[Dict[str, str]] = Field(
    None, description="Module/chart name → resolved container image reference")

# Flat dict of variable name→value (non-secret only)
resolved_variables: Optional[Dict[str, str]] = Field(
    None, description="Flat variable name→value dict (secrets excluded)")

# Git commit SHA at build time
revision: Optional[str] = Field(
    None, description="Git commit SHA at build time")
```

These are populated by the platform builder during `strata build run`:
- `layers` — copy of `spec.deployment` (already computed)
- `chart_versions` — extracted from `spec.modules[].source.version` after version-lock
  resolution (ADR 0011)
- `image_versions` — extracted from `spec.modules[].source` where source type is
  container image
- `resolved_variables` — flattened from `spec.variables[]` excluding any entries that
  came from secret stores
- `revision` — `git rev-parse HEAD` at build time

#### Why convenience fields instead of raw-only?

The raw artifact is always available — power users can iterate over `modules`,
`topologies`, `namespaces` directly. But the common case (ArgoCD ApplicationSet
parameters, Flux HelmRelease values) needs flat dicts. Without convenience fields,
every template would start with boilerplate Jinja2 loops to flatten the model:

```jinja2
{# Without convenience fields — verbose and error-prone #}
{% set chart_versions = {} %}
{% for mod in modules %}
  {% do chart_versions.update({mod.name: mod.source.version}) %}
{% endfor %}
```

With convenience fields, the same template is clean:

```jinja2
{# With convenience fields — direct access #}
"chart_version": "{{ chart_versions['integrator-core'] }}"
```

Both the raw model and convenience fields are available simultaneously.

---

## Jinja2 Adapter Templates

The rendered output format is a **user-editable Jinja2 template** in the workspace.
Operators customize their controller output without touching strata code.

### Template location

```
.strata/templates/sync/
├── README.md                          ← usage guide and variable reference
├── argocd-appset-entry.json.j2        ← ArgoCD starter
└── flux-kustomization.yaml.j2         ← Flux starter
```

The integration's `properties.template` field names the template file (resolved relative
to `.strata/templates/`). The `properties.output_file` field defines where the rendered
result is written (relative to the build output directory).

### Template variables (context)

The full platform artifact is the Jinja2 context. Templates can access any field from
`PlatformSpecModel`. The most commonly used paths:

The template context is the `PlatformSpecModel` dict (the `spec` level of the platform
artifact). Convenience fields from `meta` (like `name`) are promoted into spec so
templates don't need to reach outside `spec`. The `integration` dict is injected
separately by the provisioner.

**Convenience fields (flat, easy to use):**

| Variable             | Type             | Description                                                    |
| -------------------- | ---------------- | -------------------------------------------------------------- |
| `name`               | `str`            | Deployment name (promoted from `meta.name`)                    |
| `artifact_path`      | `str`            | Computed artifact path (e.g., `eu/contoso/default/production`) |
| `layers`             | `dict[str, str]` | Layer key-value pairs                                          |
| `tenant.code`        | `str`            | Tenant code                                                    |
| `tenant.name`        | `str`            | Tenant display name                                            |
| `chart_versions`     | `dict[str, str]` | Module name → resolved version (flat)                          |
| `image_versions`     | `dict[str, str]` | Module name → full image reference (flat)                      |
| `resolved_variables` | `dict[str, str]` | Variable name → value (secrets excluded)                       |
| `revision`           | `str`            | Git commit SHA at build time                                   |
| `labels`             | `dict[str, str]` | Workspace labels (useful for ArgoCD/Flux label selectors)      |
| `annotations`        | `dict[str, Any]` | Workspace annotations                                          |
| `namespace`          | `obj`            | Scoped namespace (when stage has `namespace:` field)           |
| `namespace.name`     | `str`            | Namespace name                                                 |
| `namespace.modules`  | `list`           | Modules declared in this namespace                             |
| `integration`        | `dict`           | The integration's `properties` block (injected by provisioner) |

**Full model access (for advanced templates):**

| Variable       | Type | Description                                                     |
| -------------- | ---- | --------------------------------------------------------------- |
| `topologies[]` | list | Topology with `.provider`, `.provisioner`, `.namespaces[]`      |
| `providers[]`  | list | Provider with `.properties.type`, `.properties.region`, `.zone` |
| `namespaces[]` | list | All namespaces (unfiltered — for multi-namespace templates)     |
| `modules[]`    | list | Modules filtered to namespace scope (or all if no scope)        |
| `variables[]`  | list | Full variable store entries (name, value, source)               |
| `resources[]`  | list | Resources with properties, storage, dependencies                |
| `stages[]`     | list | Deployment stages                                               |
| `workspace`    | obj  | Workspace identity (name, annotations, labels)                  |

**Namespace scoping behavior:**
- Stage has `namespace:` → template gets `namespace` (singular object) and `modules[]`
  filtered to that namespace's declared modules
- Stage omits `namespace:` → `namespace` is `None`, `modules[]` contains all modules,
  `namespaces[]` is available for iteration

### ArgoCD example template

```jinja2
{# .strata/templates/sync/argocd-appset-entry.json.j2 #}
{# Context: platform artifact scoped to stage namespace #}
{
  "name": "{{ tenant.code }}-{{ layers.environment }}",
  "namespace": "{{ namespace.name }}",
  "server": "{{ providers[0].properties.region }}",
  "path": "{{ artifact_path }}",
  "targetRevision": "{{ revision }}",
  "project": "{{ integration.project | default('default') }}"
  {%- for chart, version in chart_versions.items() %},
  "chart_{{ chart | replace('-', '_') }}_version": "{{ version }}"
  {%- endfor %}
  {%- for key, value in resolved_variables.items() %},
  "var_{{ key }}": "{{ value }}"
  {%- endfor %}
}
```

### Flux Kustomization example template

```jinja2
{# .strata/templates/sync/flux-kustomization.yaml.j2 #}
{# Uses scoped namespace from stage — no iteration needed #}
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: {{ tenant.code }}-{{ layers.environment }}
  namespace: {{ integration.namespace | default('flux-system') }}
  labels:
    strata.io/tenant: "{{ tenant.code }}"
    strata.io/zone: "{{ layers.zone }}"
    {%- for key, value in labels.items() %}
    {{ key }}: "{{ value }}"
    {%- endfor %}
spec:
  targetNamespace: {{ namespace.name }}
  sourceRef:
    kind: GitRepository
    name: config-repo
  path: "./{{ artifact_path }}"
  postBuild:
    substitute:
      {%- for key, value in resolved_variables.items() %}
      {{ key }}: "{{ value }}"
      {%- endfor %}
```

### Flux HelmRelease example template

```jinja2
{# .strata/templates/sync/flux-helmrelease.yaml.j2 #}
{# Iterates modules scoped to the stage's namespace #}
{% for chart, version in chart_versions.items() %}
---
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: {{ tenant.code }}-{{ layers.environment }}-{{ chart }}
  namespace: {{ namespace.name }}
  labels:
    strata.io/tenant: "{{ tenant.code }}"
spec:
  chart:
    spec:
      chart: {{ chart }}
      version: "{{ version }}"
      sourceRef:
        kind: HelmRepository
        name: charts
  values:
    {%- for key, value in resolved_variables.items() %}
    {{ key }}: "{{ value }}"
    {%- endfor %}
    {%- if chart in image_versions %}
    image:
      repository: "{{ image_versions[chart].split(':')[0] }}"
      tag: "{{ image_versions[chart].split(':')[1].split('@')[0] }}"
    {%- endif %}
{% endfor %}
```

---

## Reconciliation Health

`strata deploy health` auto-detects stages that use a `sync`-capable provisioner and
queries the controller for reconciliation status. No `--gitops` flag needed.

### Reconciliation result (shared across providers)

```python
@dataclass
class ReconciliationResult:
    sync_status: str          # Synced | OutOfSync | Unknown
    health_status: str        # Healthy | Degraded | Progressing | Suspended
    last_synced_at: datetime | None
    revision: str | None      # git SHA the controller last reconciled
    intended_revision: str    # git SHA strata expects
    drift: bool               # revision != intended_revision
    message: str | None       # controller's status message
```

### Provider-specific health queries

| Provider   | How strata queries status                                                                    |
| ---------- | -------------------------------------------------------------------------------------------- |
| **ArgoCD** | REST API: `GET /api/v1/applications/{name}` → `.status.sync.status`, `.status.health.status` |
| **Flux**   | Kubernetes API: `.status.conditions[]` on Kustomization/HelmRelease with type `Ready`        |

### Audit integration

The reconciliation result is appended to the deployment's audit record (ADR 0018),
completing the traceability chain:
strata intended → strata applied → controller reconciled.

---

## Scaffolding via `strata sln init`

When a `sync`-capable integration is present in the configuration, `strata sln init`
creates starter templates and a README:

```
.strata/templates/sync/
├── README.md                          ← usage guide and variable reference
├── argocd-appset-entry.json.j2        ← (if type: argocd)
└── flux-kustomization.yaml.j2         ← (if type: flux)
```

The README documents all available template variables, Jinja2 usage tips, and
configuration instructions. See the template variables table above for the full reference.

---

## Decisions

1. **Provisioner model** — ArgoCD and Flux are provisioner types (`provisioner: argocd`,
   `provisioner: flux`) within existing deployment stages. No new CLI commands.
2. **Capability name: `sync`** — the integration declares `capabilities: [sync]`. The
   provisioner renders templates at build time and verifies reconciliation at health time.
3. **Jinja2 adapter templates** — the output format is a user-editable template in
   `.strata/templates/sync/`. Operators customize without code changes.
4. **Pull model for health** — strata polls the controller API during `deploy health`.
   Webhooks are out of scope.
5. **File-based output** — strata writes rendered output to git. The controller reads
   from git. Strata never writes directly to the cluster.
6. **Secret suppression** — variables resolved from secret stores are excluded from the
   rendered template context. Only non-secret variables appear in `resolved_variables`.
7. **Partial deployment separation** — `deploy-infra` (terraform stages) and `deploy-apps`
   (sync stages) can be separate partial bases, giving teams clean separation of concerns.
8. **Platform artifact IS the template context** — no separate "universal manifest." The
   Jinja2 template receives the full `PlatformSpecModel` dict. Convenience fields
   (`layers`, `chart_versions`, `image_versions`, `resolved_variables`, `revision`) are
   added to the model for ergonomic template access. Power users access the full model.
9. **Stage backend binds to integration by name** — `backend.integration` on the stage
   names the integration instance. This allows multiple instances of the same type
   (e.g., `argocd-prod` vs `argocd-staging`).
10. **Stage backend specifies deploy target via remote** — `backend.remote` names the
    strata remote (`strata repo add`) where rendered output is committed. The deployment
    controls where output goes, not the integration. Partial bases define it once; leaves
    inherit via `spec.extends`.
11. **Scaffolding via `sln init` and `sln update`** — starter templates are created when
    a `sync`-capable integration is detected. `sln update` adds templates for integrations
    added after initial setup.
12. **Stage `namespace` scopes the sync** — the stage's `namespace` field references one
    of `workspace.spec.namespaces[].name`. The provisioner filters modules to those
    declared in that namespace. If omitted, all namespaces/modules are included and the
    template is responsible for iteration.

---

## Consequences

- No new CLI surface — ArgoCD/Flux integrate through the existing build/deploy/health
  lifecycle as provisioner types.
- Teams can split infrastructure (terraform) from applications (argocd/flux) using partial
  deployment files, or combine them in a single deployment with multiple stages.
- The Jinja2 template approach means strata is not coupled to any specific controller
  resource format — operators own the output shape.
- `strata deploy health` reports reconciliation status alongside infrastructure health,
  giving a unified view of deployment state.
- Promotion workflows (ADR 0011) work unchanged — the version-lock file is resolved
  into the template context, and the controller deploys exactly what strata decided.
- Adding a new GitOps provider requires only a new provisioner class and a starter
  template — the universal manifest contract and health interface remain stable.
- The platform artifact gains convenience fields (`layers`, `chart_versions`,
  `image_versions`, `resolved_variables`, `revision`) that benefit all provisioner
  types and template consumers, not just sync provisioners.
- Stage `backend.remote` reuses the existing strata remote system — no new plumbing
  for config repo targeting.