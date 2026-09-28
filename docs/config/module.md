# Module (`kind: module`)

A deployable workload: containers/services, environment variables, mounts, health checks. Built
from either a Helm chart or a Compose-style service list, sourced from a remote.

## Schema

- `spec.source` — a [`SourceModel`](solution.md) reference: `remote` + either `chart_name`/
  `chart_version` (Helm) or `source_path` (Compose/copied files)
- `spec.type` — the deployer tool (e.g. `helm`, `compose`, `argocd`, or a custom provisioner
  plugin name) — required for service deployment commands
- `spec.release_name` — optional Helm release/ArgoCD Application name (defaults to `meta.name`)
- `spec.kubernetes_namespace` — optional target namespace override (defaults to the strata
  namespace name)
- `spec.compose_file` — reference an external `docker-compose.yml` verbatim instead of generating
  one from `spec.services` — mutually exclusive with `spec.services`
- `spec.files[]` — files/globs copied verbatim into the module's build output, each `{source, target}`
  (`source` supports `@repo/...` cross-remote references)
- `spec.services[]` — one per container/process, each:
  - `name`, `image` (or `artifact` — an [`artifact`](artifact.md) document name; mutually exclusive
    with `image`), `restart`, `command`, `depends_on[]`, `ports[]`
  - `environment[]` — each `{key, value}`; `value` is a literal or a
    `${var:}`/`${secret:}`/`${feature:}` token
  - `mounts[]` — bind mounts, Docker named volumes (`volume_ref`), or Kubernetes PVCs
    (`storage_class`/`storage_size`/`access_mode`)
  - `healthcheck` — `{name, type, command|endpoint, interval, timeout, retries}`
  - `configuration` — deployer-specific overrides merged verbatim (into the Compose service block,
    or `values.<service.name>` for Helm)
- `spec.properties` — single-service backward-compatible shape (`mounts[]`/`checks[]`/`endpoints[]`)
  used when `spec.services` is absent
- `spec.default_labels` / `spec.custom_labels` — Kubernetes-style labels
- `spec.lifecycle` — optional IaC workflow hook phases

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: module
meta:
  name: portainer
spec:
  source:
    remote: bundled
    source_path: portainer
  type: compose
  default_labels:
    app: portainer
  services:
    - name: portainer
      image: portainer/portainer-ce:2.39.3-alpine
      restart: unless-stopped
      ports:
        - "8000:8000"
      environment:
        - key: TZ
          value: Europe/Brussels
      mounts:
        - name: data
          type: bind
          source_path: /opt/haven/var/data/portainer
          target_path: /data
```

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: module
meta:
  name: authentik
spec:
  source:
    remote: charts
    chart_name: authentik
    chart_version: "2026.5.2"
  type: helm
  default_labels:
    app: authentik
  services:
    - name: db
      image: docker.io/library/postgres:16-alpine
      environment:
        - key: POSTGRES_PASSWORD
          value: "${secret:AUTHENTIK_POSTGRESQL__PASSWORD}"
      mounts:
        - name: data
          storage_class: standard
          storage_size: 10Gi
```

## Notes

- A mount's `volume_ref` (Docker named volume) and `storage_class` (Kubernetes PVC) are mutually
  exclusive — pick the one matching the module's `type`.
- `default_labels`/`custom_labels` (not `default_tags`/cloud tags): a module ultimately becomes a
  Kubernetes or Compose workload, not an individually cloud-tagged resource.
