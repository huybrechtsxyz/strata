# Asset (`kind: asset`)

A named, reusable file-set location for plain, non-code input files a provisioner needs at build
time (an OpenAPI contract, a certificate bundle, images, seed data) whose on-disk variant differs
per deployment. Declared in a workspace's `spec.assets` pool, then selected per execution step via
`step.assets` — the same pool-then-select pattern `namespaces`/`firewalls`/`dns_zones`/`networks`
already use. Materialised into the build output by `strata build run`, before any execution step
runs.

## Schema

- `spec.source_path` — path to the file or directory, relative to the resolved remote's root. May
  contain `{name}` segment tokens, filled from the deployment's resolved `spec.properties` at
  build time (e.g. `assets/{customer}/{deploy_tier}/openapi`) — no fixed token vocabulary, any
  `properties` key a workspace already uses works
- `spec.remote` — optional [remote](solution.md) this asset's files live under; omit for this
  solution's own repository
- `spec.description` — optional description for documentation purposes

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: asset
meta:
  name: openapi
spec:
  description: "OpenAPI contract files for the Contoso API Gateway"
  source_path: "assets/{customer}/{deploy_tier}/openapi"
```

Declared in a workspace's pool, then selected by the step that needs it:

```yaml
spec:
  assets: [openapi]
  execution:
    - name: publish_api
      provisioner: terraform_apim
      targets: [apim]
      assets: [openapi]
```

Materialised at build time to `build_path/assets/openapi/`, and projected into every consuming
step's `var.strata` as `strata.assets.openapi.{path, files}` — a provisioner's own code can read
`local.strata.assets.openapi.files` instead of globbing a hardcoded relative path.

## Notes

- Deliberately **not** built on [`SourceModel`](solution.md) despite the surface similarity
  (`remote`/`source_path`) — `SourceModel` also carries Helm chart-registry mode, which never
  applies to a plain file copy.
- Not the same as [`artifact`](artifact.md): an artifact is a container image reference strata
  never fetches (the registry pulls it at deploy time); an asset is the opposite — strata **must**
  copy real files onto disk at build time.
- A missing `{name}` token with no matching `spec.properties` key, or a missing on-disk variant
  after filling, is a build-time error only — not also a `strata validate` Phase 2 warning,
  matching every other kind (Phase 2 does no real filesystem check for any kind today).
