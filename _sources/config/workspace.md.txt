# Workspace (`kind: workspace`)

The composed "image" (ADR-0011): a static, versioned declaration of everything a solution could
deploy — providers, resources, topologies, namespaces, and the provisioning recipe that builds/
deploys them. A [`deployment`](deployment.md) (the "container instance") executes that recipe
against a specific environment; it never invents new tool bindings, only supplies runtime
parameters.

## Schema

- `spec.providers[]` — provider **slots**: each is a [`provider`](provider.md) document name, or
  `{slot, provider}` to give the slot a stable name distinct from its default document. IaC code
  addresses the slot (`strata.providers.<slot>`); a [`deployment`](deployment.md) can rebind it to
  another provider document of the same type — see "Provider slots" below
- `spec.assets[]` — asset **slots**: each is an [`asset`](asset.md) document name, or `{slot, asset}`
  to give the slot a stable name distinct from its default document. A step selects slots through
  its own `assets`, and a [`deployment`](deployment.md) can rebind a slot — see "Asset slots" below
- `spec.resources[]` — workspace-local resource instances, each:
  - `name` — the workspace-local instance name
  - `resource` — the [`resource`](resource.md) document it's built from (or `managed_by: provisioner`
    if fully externally managed)
  - `role` — matched against the topology type's expected component roles
  - `count` — number of instances (default 1)
  - `depends_on[]`, `firewalls[]`, `subnet` — workspace-specific gluing
  - `enabled` — whether this instance is included in the built artifact (default `true`)
- `spec.namespaces[]` — names of [`namespace`](namespace.md) documents
- `spec.topology[]` — inline topology groupings (type/components/namespaces/volumes) — see
  "Topology grouping" below. Inline since ADR-0028 (reverted from a standalone `kind: topology`
  document, ADR-0011) — not a list of names.
- `spec.provisioners[]` — tool definitions (`ProvisionerModel`): `name`, `tool` (terraform, ansible,
  helm, compose, ...), `source`, `backend` (terraform only), `properties` (ansible only),
  `integration` (which [`integration`](integration.md) document supplies version/auth), `output`
  (Jinja2 template escape hatch)
- `spec.execution[]` — the ordered recipe (`ProvisioningStepModel`): `name`, `provisioner`,
  `resources[]`/`namespaces[]` (direct references this step acts on), `topologies[]` (expands to
  that topology's own resources/namespaces), `dns_zones[]`/`firewalls[]`/`networks[]` (direct
  escape hatch for claiming one of those documents' `${output:...}` ownership without a
  resource/topology link), `depends_on[]` for step ordering. At least one of the six target-ish
  fields is required. A resource's own `firewalls`/`dns_zones`/`subnet.network` are claimed
  automatically once that resource is effectively in scope (direct or via `topologies[]`) — no
  need to also list them directly on the step.
  `assets[]` selects the asset slots this step needs (see "Asset slots").
- `spec.default_tags` — default cloud tags for resources created by this workspace
- `spec.cve_policy` — fail `build run` when the SBOM's CVE findings at or above `max_severity`
  exceed `max_count` (scanned with Trivy or Grype)
- `spec.checkov_policy` — fail `build run` on Checkov findings at or above `severity_gate`; a
  finding with no severity (open-source Checkov reports none) always counts
- `spec.release_gate_policy` — at deploy, ask a `release_gate` [integration](integration.md) for its
  verdict on `repository` at the deployed commit
- `spec.image_gate_policy` — at deploy, count an `image_findings` integration's open issues for every
  image in `sbom.json` against `max_severity`/`max_count`

  Every policy has `enforcement` (`deny` | `warn`) and `on_missing_data` (`skip` | `warn` |
  `block`, default `block`). `strata policy list` shows what a deployment's workspace configures;
  `strata policy check` evaluates the two deploy gates on demand. See
  [how-to/security-gates.md](../how-to/security-gates.md).

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: workspace
meta:
  name: main
spec:
  providers:
    - slot: azure
      provider: azure-main
  namespaces:
    - hearth
  provisioners:
    - name: terraform-main
      tool: terraform
      source:
        remote: infra
        source_path: terraform/main
      backend:
        type: azurerm
        configuration:
          resource_group_name: tfstate-rg
  resources:
    - name: web-storage
      resource: storage-account
      role: control-plane
    - name: web-vm
      resource: linux-vm
      role: worker
      count: 2
  execution:
    - name: provision-infra
      provisioner: terraform-main
      resources:
        - web-storage
        - web-vm
  topology:
    - name: main-topology
      type: kubernetes
      components:
        - resource: web-storage
        - resource: web-vm
  default_tags:
    managed-by: strata
```

## Provider slots

A `spec.providers[]` entry is a named **slot** with a default provider document:

```yaml
providers:
  - slot: azure              # stable key — IaC code addresses strata.providers.azure
    provider: azure-main     # default Provider document (westeurope)
  - azure-shared             # bare name = slot and document share the name
```

- `slot` is unique within the workspace; two slots may point at the same document.
- A bare string is shorthand for `{slot: <name>, provider: <name>}`, so a workspace that never
  rebinds anything reads exactly as before and keeps its `strata.providers.<name>` keys.
- The same workspace can target another region or account without its IaC code changing: a
  [`deployment`](deployment.md) rebinds the slot (`spec.providers[]` there). Prefer an explicit
  slot name (`azure`) over one named after a specific region's document (`azure-main`) when you
  expect to rebind — otherwise `strata.providers.azure-main` would silently point at another
  region.

## Asset slots

A `spec.assets[]` entry is a named **slot** with a default [`asset`](asset.md) document, and an
execution step selects the slots it needs:

```yaml
assets:
  - slot: api                # stable key — steps select it, IaC code reads local.strata_assets.api
    asset: api-default       # default Asset document
  - openapi                  # bare name = slot and document share the name
execution:
  - name: publish_api
    provisioner: terraform_apim
    resources: [apim]
    assets: [api]            # selects the slot
```

- `slot` is unique within the workspace; two slots may point at the same document. A step
  selects a **slot**, never a document name directly.
- A bare string is shorthand for `{slot: <name>, asset: <name>}`, so a workspace that never
  rebinds anything reads exactly as before.
- Only slots a step selects are materialised and projected, so an unused slot costs nothing.
- A [`deployment`](deployment.md) rebinds the slot to another asset document, so one workspace can
  serve many customers whose files live in different repositories.

## Topology grouping

Each `spec.topology[]` entry is a named grouping of resources and namespaces that conceptually
belong together (e.g. "the AKS cluster + its blob store + its key vault") — for documentation,
diagramming, and workload placement. It deliberately carries no provider/provisioner binding —
which tool builds/deploys these resources is a separate concern (`spec.execution`/`provisioners`
above): a single provisioner can span several groupings, and a single grouping can be built by
several different provisioners acting on different subsets of it (ADR-0011).

- `name` — unique grouping name within this workspace
- `type` — the topology type (must match a [topologyconfig.md](topologyconfig.md) document's
  `meta.name`)
- `components[]` — each `{resource, modules}`: a reference to this workspace's own
  `spec.resources[].name` (checked at Phase 1 — only possible because the grouping is inline now,
  ADR-0028), optionally with application code (`modules[]`, same shape as
  [namespace.md](namespace.md)'s module references) attached directly to that resource (e.g. an
  Azure Function App's code onto its Function App resource)
- `namespaces[]` — each `{namespace}`: a reference to this workspace's own `spec.namespaces[]`
- `volumes[]` — topology-level storage volumes

When multiple modules are attached to one component and more than one is enabled, exactly one
must be marked as the `main` slot.

## Notes

- Three sibling keys answer three different questions: `providers` (where — which account, as
  rebindable slots), `provisioners` (with what — tool definitions), `execution` (what runs, in
  what order).
- `execution` names steps ordered by `depends_on`, not a numeric `priority` (ADR-0011) — `strata
  deploy run` executes steps in that dependency order.
- A [`deployment`](deployment.md)'s `stages[]` reference `execution[].name` for runtime parameters
  (timeouts, health checks, enabled/disabled) — they never redefine what runs.
