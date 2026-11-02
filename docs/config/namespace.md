# Namespace (`kind: namespace`)

A named collection of modules — the Kubernetes-native grouping unit modules are placed into.

## Schema

- `spec.type` — `dedicated` (default; belongs to a single deployment layer, cross-layer overlap is
  a warning) or `shared` (intentionally used by multiple layers, e.g. `kube-system`; overlap is suppressed)
- `spec.modules[]` — each `{name, module}`: a workspace-local instance name plus the
  [`module`](module.md) document it's built from
- `spec.configuration` — raw passthrough (e.g. namespace annotations/quotas not otherwise modeled)
- `spec.default_labels` / `spec.custom_labels` — labels on the namespace
  object itself (distinct from labels on the modules placed inside it)
- `spec.lifecycle` — optional IaC workflow hook phases

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: namespace
meta:
  name: hearth
spec:
  default_labels:
    app: hearth
  modules:
    - name: authentik
      module: authentik
    - name: portainer
      module: portainer
```

## Notes

- A namespace must declare `lifecycle` and/or `modules` — an empty namespace (neither) is rejected.
- `default_labels`/`custom_labels`, not cloud tags — a namespace is a Kubernetes concept, not a
  provisioned ARM/AWS resource.
