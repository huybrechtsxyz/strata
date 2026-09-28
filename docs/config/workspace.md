# Workspace (`kind: workspace`)

The composed "image" (ADR-0011): a static, versioned declaration of everything a solution could
deploy — providers, resources, topologies, namespaces, and the provisioning recipe that builds/
deploys them. A [`deployment`](deployment.md) (the "container instance") executes that recipe
against a specific environment; it never invents new tool bindings, only supplies runtime
parameters.

## Schema

- `spec.providers[]` — names of [`provider`](provider.md) documents this workspace uses
- `spec.resources[]` — workspace-local resource instances, each:
  - `name` — the workspace-local instance name
  - `resource` — the [`resource`](resource.md) document it's built from (or `managed_by: provisioner`
    if fully externally managed)
  - `role` — matched against the topology type's expected component roles
  - `count` — number of instances (default 1)
  - `depends_on[]`, `firewalls[]`, `subnet` — workspace-specific gluing
  - `enabled` — whether this instance is included in the built artifact (default `true`)
- `spec.namespaces[]` — names of [`namespace`](namespace.md) documents
- `spec.topology[]` — names of [`topology`](topology.md) documents
- `spec.provisioners[]` — tool definitions (`ProvisionerModel`): `name`, `tool` (terraform, ansible,
  helm, compose, ...), `source`, `backend` (terraform only), `properties` (ansible only),
  `integration` (which [`integration`](integration.md) document supplies version/auth), `output`
  (Jinja2 template escape hatch)
- `spec.execution[]` — the ordered recipe (`ProvisioningStepModel`): `name`, `provisioner`,
  `targets[]` (resource/namespace names), `depends_on[]` for step ordering
- `spec.default_tags` — default cloud tags for resources created by this workspace

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: workspace
meta:
  name: main
spec:
  providers:
    - azure-main
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
      targets:
        - web-storage
        - web-vm
  topology:
    - main-topology
  default_tags:
    managed-by: strata
```

## Notes

- Three sibling keys answer three different questions: `providers` (where — which account),
  `provisioners` (with what — tool definitions), `execution` (what runs, in what order).
- `execution` names steps ordered by `depends_on`, not a numeric `priority` (ADR-0011) — `strata
  deploy run` executes steps in that dependency order.
- A [`deployment`](deployment.md)'s `stages[]` reference `execution[].name` for runtime parameters
  (timeouts, health checks, enabled/disabled) — they never redefine what runs.
