# Helm Modules

How to define, build, and deploy Helm charts with strata.

## Overview

Strata handles the full Helm lifecycle: **define → build → deploy**.  You
define what you want in YAML, strata builds self-contained artifacts, then
deploys them to your Kubernetes cluster.

```
strata build run --file config/deploy-prod.yaml   → artifacts
strata deploy run --file config/deploy-prod.yaml  → helm upgrade --install
```

---

## Defining a Helm Module

A module YAML tells strata: "I want this chart, at this version, with these
values, deployed to this namespace."

### Registry chart (third-party)

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: module
meta:
  name: argocd
  annotations:
    description: Argo CD — GitOps continuous delivery
spec:
  source:
    chart_name: argo-cd
    chart_version: "7.8.0"
    chart_repository: https://argoproj.github.io/argo-helm
  type: helm
  release_name: argocd
  kubernetes_namespace: argocd
  files:
    - source: services/forge/argocd/values.yaml
      target: values.yaml
```

### Local chart (your own)

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: module
meta:
  name: my-app
spec:
  source:
    repository: my-repo
    source_path: services/my-app/helm
  type: helm
  release_name: my-app
  kubernetes_namespace: apps
  files:
    - source: services/my-app/values-prd.yaml
      target: values.yaml
```

### Source modes

| Mode               | Fields                                                     | When to use                                                    |
| ------------------ | ---------------------------------------------------------- | -------------------------------------------------------------- |
| **Registry chart** | `chart_repository` + `chart_name` + `chart_version`        | Third-party charts (ArgoCD, nginx-ingress, cert-manager, etc.) |
| **Local chart**    | `repository` + `source_path` (directory with `Chart.yaml`) | Your own Helm charts stored in a config repo                   |

### Spec fields reference

| Field                  | Purpose                                                  | Default               |
| ---------------------- | -------------------------------------------------------- | --------------------- |
| `release_name`         | Helm release name                                        | `meta.name`           |
| `kubernetes_namespace` | Target Kubernetes namespace                              | Strata namespace name |
| `configuration`        | Extra helm values merged into `values.yaml`              | —                     |
| `files`                | Files to copy into build output (values, etc.)           | —                     |
| `services`             | Structured service definitions → generates `values.yaml` | —                     |

---

## Wiring into a Deployment

### 1. Namespace references the module

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: namespace
meta:
  name: forge
spec:
  modules:
    - name: argocd
      file: config/forge/modules/mod-argocd.yaml
```

### 2. Workspace topology references the namespace

```yaml
# In workspace spec.topologies:
- name: hetzner_forge
  provider: hetzner_dc_eu_de
  provisioner: haven_iac
  type: single_node
  namespaces:
    - namespace: forge
```

### 3. Deployment stage references the topology

```yaml
# In deployment spec.stages:
- name: services_forge
  topology: hetzner_forge
  scope: all
  depends_on: infrastructure_forge
  on_failure: stop
```

---

## Build Output

```bash
strata build run --file config/deploy-prod.yaml
```

Produces a self-contained artifact per module:

```
build/<deployment>/<namespace>/<module>/
├── meta.yaml       ← deployment identity + chart coordinates
└── values.yaml     ← helm values
```

### meta.yaml

Contains everything needed to deploy — no source YAML required at deploy time:

```yaml
releaseName: argocd
namespace: argocd
chartName: argo-cd
chartVersion: "7.8.0"
chartRepository: https://argoproj.github.io/argo-helm
```

For local charts, `chartName`/`chartVersion`/`chartRepository` are absent.
The deployer uses the build directory as the chart reference instead.

### values.yaml

Helm values assembled from three possible sources (in merge order):

| Source               | Use case                                                            |
| -------------------- | ------------------------------------------------------------------- |
| `spec.services`      | Structured definitions (env vars, mounts, ports) → generated values |
| `spec.configuration` | Extra top-level values merged on top                                |
| `spec.files`         | Hand-crafted values.yaml copied verbatim                            |

For registry charts without `services` (like ArgoCD), use `files` to provide
your own values.yaml.  For your own apps with structured services, use
`services` + optionally `configuration` for overrides.

---

## Deploy

```bash
strata deploy run --file config/deploy-prod.yaml
```

### What executes

| Step      | Command                                                   | Purpose                          |
| --------- | --------------------------------------------------------- | -------------------------------- |
| **setup** | `helm repo add` + `helm repo update`                      | Register chart repositories      |
| **plan**  | `helm upgrade --dry-run --install ...`                    | Preview changes without applying |
| **apply** | `helm upgrade --install --wait --atomic --timeout 5m ...` | Install or upgrade the release   |

### Safety flags

- `--wait` — blocks until all pods are Running (not just submitted)
- `--atomic` — automatically rolls back if the deploy fails
- `--timeout 5m` — gives complex charts time to stabilize
- `--create-namespace` — creates the target namespace if it doesn't exist

### Dry-run only

```bash
strata deploy run --file config/deploy-prod.yaml --plan-only
```

### Destroy

```bash
strata deploy run --file config/deploy-prod.yaml --destroy --force
```

Runs `helm uninstall <release> --namespace <namespace>` for each module.

---

## GitOps Integration

Strata deploys Helm charts directly.  It does **not** generate ArgoCD
`Application` CRs or Flux `HelmRelease` CRs — that is out of scope.

If you want GitOps after deploying ArgoCD:

- **Monitoring mode:** ArgoCD discovers existing helm releases strata deployed
  (zero extra config)
- **Application CRs:** Write them yourself — `meta.yaml` has all the fields
  you need to fill in the template
- **App-of-apps:** Maintain an umbrella chart that generates Applications from
  strata's values files
- **Git generator:** Point ArgoCD's `ApplicationSet` at the build output
  directory

Strata's role: deploy ArgoCD.  ArgoCD's role: manage everything else.

---

## Examples

### Minimal — deploy a registry chart with custom values

```yaml
spec:
  source:
    chart_name: nginx-ingress
    chart_version: "4.10.0"
    chart_repository: https://kubernetes.github.io/ingress-nginx
  type: helm
  release_name: ingress
  kubernetes_namespace: ingress-nginx
  files:
    - source: services/ingress/values.yaml
      target: values.yaml
```

### Structured services — generate values from definitions

```yaml
spec:
  source:
    repository: my-repo
    source_path: charts/my-app
  type: helm
  release_name: my-app
  kubernetes_namespace: production
  services:
    - name: api
      image: my-app:1.2.0
      environment:
        - key: DATABASE_URL
          secret: DB_URL
        - key: LOG_LEVEL
          value: info
      mounts:
        - name: data
          storage_class: fast-ssd
          storage_size: 10Gi
          target_path: /var/data
  configuration:
    replicaCount: 3
    resources:
      limits:
        memory: 512Mi
```

Produces:

```yaml
# values.yaml (generated)
my-app-api:
  env:
    DATABASE_URL: "${DB_URL}"
    LOG_LEVEL: info
  persistence:
    data:
      storageClass: fast-ssd
      accessMode: ReadWriteOnce
      size: 10Gi
replicaCount: 3
resources:
  limits:
    memory: 512Mi
```
