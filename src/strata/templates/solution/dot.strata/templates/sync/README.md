# Sync Templates

Starter Jinja2 templates for GitOps controller stages (ArgoCD and Flux).

These files are read by `strata build run` when a deployment stage has a
`backend.integration` that references a sync-capable integration.

---

## How it works

1. **Configuration** — declare a sync integration in your `configuration.yaml`:

```yaml
spec:
  integrations:
    - name: my_argocd
      type: argocd
      capabilities: [sync]
      properties:
        template: sync/argocd-appset-entry.json.j2   # path under .strata/templates/
        output_file: gitops/apps/entry.json           # path written into build output
        app_name: my-app
        project: default
        repo_url: https://github.com/org/gitops-repo
        target_revision: main
        path: clusters/prd/apps
        destination_namespace: production
```

2. **Deployment** — reference the integration in a stage:

```yaml
spec:
  stages:
    - name: gitops
      provisioner: argocd
      backend:
        integration: my_argocd
        remote: gitops-repo        # registered via `strata repo add`
      namespace: backend           # optional: scope to a single namespace
```

3. **Build** — `strata build run` renders the template and writes the output:

```
.strata/build/<deployment>/<stage-name>/<output_file>
```

4. **Deploy** — `strata deploy run` commits the rendered file to the remote
   GitOps repository and pushes, triggering reconciliation.

---

## Template context

Every template receives the full platform artifact as Jinja2 variables:

| Variable             | Type | Description                                           |
| -------------------- | ---- | ----------------------------------------------------- |
| `name`               | str  | Deployment name                                       |
| `labels`             | dict | Deployment labels                                     |
| `annotations`        | dict | Deployment annotations                                |
| `layers`             | dict | Layer values (environment, tenant, …)                 |
| `revision`           | str  | Resolved version string                               |
| `modules`            | list | Platform modules (dicts)                              |
| `namespaces`         | list | Platform namespaces (dicts)                           |
| `chart_versions`     | dict | Chart name → version map                              |
| `image_versions`     | dict | Image name → tag map                                  |
| `resolved_variables` | dict | Resolved platform variables (secrets excluded)        |
| `integration`        | dict | `spec.integrations[].properties` for the active stage |
| `namespace`          | dict | Scoped namespace (only when `stage.namespace` is set) |

---

## ArgoCD — `argocd-appset-entry.json.j2`

Produces a JSON object for an
[ApplicationSet list generator](https://argo-cd.readthedocs.io/en/stable/operator-manual/applicationset/Generators-List/).

Required integration properties:
- `repo_url` — Git repository URL
- `path` — path inside the repository

Optional:
- `app_name` (default: deployment name)
- `project` (default: `"default"`)
- `target_revision` (default: build `revision` or `"HEAD"`)
- `destination_namespace` (default: `layers.environment`)
- `destination_server` (default: `"https://kubernetes.default.svc"`)

---

## Flux — `flux-kustomization.yaml.j2`

Produces a Flux
[Kustomization](https://fluxcd.io/flux/components/kustomize/kustomizations/) resource.

Required integration properties:
- `source_ref` — name of the `GitRepository` source object

Optional:
- `resource_name` (default: deployment name)
- `flux_namespace` (default: `"flux-system"`)
- `path` (default: `"./kustomize"`)
- `interval` (default: `"5m0s"`)
- `timeout` (default: `"3m0s"`)
- `prune` (default: `true`)
- `destination_namespace` (default: `layers.environment`)

---

## Customising these templates

These files are package-owned and will be refreshed by `strata sln update`.
To create a custom variant, copy the file with a different name:

```
cp .strata/templates/sync/flux-kustomization.yaml.j2 \
   .strata/templates/sync/flux-helmrelease.yaml.j2
```

Then reference it from your integration's `properties.template`.
