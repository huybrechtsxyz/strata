# Asset (`kind: asset`)

A named, reusable file-set location for plain, non-code input files a provisioner needs at build
time (an OpenAPI contract, a certificate bundle, images, seed data) whose files differ per
deployment. A [`workspace`](workspace.md) declares **asset slots** with a default asset document,
each execution step selects the slots it needs, and a [`deployment`](deployment.md) can rebind a
slot to another asset document. `strata build run` copies the files into the build output before
any execution step runs.

## Schema

- `spec.source_path` — path to the file or directory, relative to the root of wherever the files
  live. May contain `{name}` segment tokens, filled from the deployment's resolved
  `spec.properties` at build time (e.g. `assets/{customer}/{deploy_tier}/openapi`) — no fixed token
  vocabulary, any `properties` key a workspace already uses works. May also be a glob
  (`openapi/**/*.json`) to select a subset of files
- `spec.remote` — optional [remote](solution.md) declared in `strata.yaml` that the files live
  under; omit for this solution's own repository
- `spec.location` — optional git repository the files live in, declared on the asset itself
  instead of as a manifest remote. Mutually exclusive with `remote`:
  - `url` — the repository
  - `reference` — branch, tag or commit SHA (required)
  - `type` — only `git` (the default)
  - `fetch` — `strata` (default) clones it; `external` means CI already checked it out at
    `.strata/assets/<asset name>/<reference>` and strata only reads it
  - `integration` — not supported yet; setting it is an error, not silently ignored. Use
    `fetch: external` or ambient git credentials
- `spec.description` — optional description for documentation purposes

## Examples

An asset whose files are in this solution's own repository, or in a remote:

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: asset
meta:
  name: api-default
spec:
  description: "OpenAPI contract files for the Contoso API Gateway"
  remote: infra
  source_path: "assets/{deploy_tier}/openapi"
```

An asset that carries its own repository — the definition lives in the config repository, the
files in the customer's:

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: asset
meta:
  name: c0062-api
spec:
  location:
    type: git
    url: https://git.example.com/customers/c0062-api.git
    reference: v1.4.0
  source_path: openapi
```

## Using an asset

The workspace names a slot, a step selects it, and a deployment may rebind it
(see [workspace](workspace.md) "Asset slots" and [deployment](deployment.md) "Rebinding an asset"):

```yaml
# workspace
spec:
  assets:
    - slot: api
      asset: api-default
  execution:
    - name: publish_api
      provisioner: terraform_apim
      resources: [apim]
      assets: [api]
---
# deployment
spec:
  extends: deploy-base
  assets:
    - slot: api
      asset: c0062-api
```

The files are copied to `build_path/assets/<slot>/` — keyed by the slot, so the path is the same
whichever document is bound. Rebuilding into a build path that already holds an earlier build
replaces that slot's directory, so files from a previously bound document never linger.

### From Terraform

Read the slot through `local.strata_assets`, which strata generates for you:

```hcl
file(local.strata_assets.api.files["openapi.json"])      # one file
fileset(local.strata_assets.api.dir, "**/*.json")        # or search the directory
```

The same code works for every customer; only the bound asset document differs. The paths are
relative to the Terraform root module's directory, so they keep working wherever the build output
is moved. Wrap `dir` in `abspath()` if something needs an absolute path.

`local.strata_assets` is generated alongside `local.strata`; a root module that declares its own
`variable "strata"` opts out of both and takes care of its own paths.

## Versions

A [`version`](version.md) document can move an asset that declares its own `location` to another
git reference without editing the asset document:

```yaml
spec:
  pins:
    assets:
      c0062-api: v1.5.0
```

A pin only applies to an asset with a `location` and `fetch: strata`. An asset that uses a manifest
`remote` is pinned under `pins.remotes` instead.

## Validation

- A slot a deployment rebinds must be declared by its workspace: `asset_slot_unknown` (error)
- Rebinding a slot to its own default has no effect: `asset_binding_redundant` (warning)
- A bound or default asset document that does not exist is an `unknown_reference`
- A missing `{name}` token with no matching `spec.properties` key, a path or glob that matches
  nothing, or a repository that cannot be fetched is a build-time error only

## Notes

- Deliberately **not** built on [`SourceModel`](solution.md) despite the surface similarity
  (`remote`/`source_path`) — `SourceModel` also carries Helm chart-registry mode, which never
  applies to a plain file copy.
- Not the same as [`artifact`](artifact.md): an artifact is a container image reference strata
  never fetches (the registry pulls it at deploy time); an asset is the opposite — strata **must**
  copy real files onto disk at build time.
- `strata build run` must have run before `strata deploy run`: deploy reads the asset directories
  the build produced and reports a missing one instead of projecting an empty `strata.assets`.
- Design and history: [docs/design/asset-management.md](../design/asset-management.md).
