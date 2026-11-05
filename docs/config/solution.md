# Solution (`kind: solution`)

The bootstrap document — `strata.yaml` at the solution root. Marks "a strata solution starts
here" and supplies the minimum needed to find everything else: discovery rules and remotes live
downstream of it, never inside it. Found by walking up from any path (like `go.mod`/`package.json`),
not by discovery itself.

## Schema

- `spec.configuration` — relative path to this solution's `configuration.yaml` (adjacent by convention)
- `spec.discovery.exclude` — glob patterns excluded from document discovery, additive to the
  loader's built-in ignores (`.git`, `build`, `.strata`, tool caches)
- `spec.remotes[]` — named external artifact sources, each:
  - `name` — referenced elsewhere as `@<name>/<path>`
  - `type` — `local`, `git`, `oci`, or `helm`
  - `url` — the source location; must not embed a password or token (use an integration for credentials)
  - `reference` — the branch, tag or commit SHA the remote is taken at. A `git` remote needs this
    or `path`, never both
  - `path` — `git` only: where a pipeline or developer already checked the repository out. Relative to
    the solution root (the folder holding `strata.yaml`), `..` allowed, or absolute. Strata reads it
    there and never clones it; see
    [A repository your pipeline checked out](#a-repository-your-pipeline-checked-out)
  - `fetch` — `strata` (strata materializes it) or `external` (already placed by CI/elsewhere).
    `path` implies `external`
  - `integration` — optional name of an [integration](integration.md) providing credentials; it must
    exist and declare the `sources` capability, or validation fails. Not valid for `type: local`.
    See [integrations/git.md](../integrations/git.md) for a private git remote and
    [integrations/chart-registries.md](../integrations/chart-registries.md) for a `helm`/`oci` registry
  - `description` — optional, human-readable

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: solution
meta:
  name: example
  annotations:
    description: "Example solution used to exercise the discovery loader and the full strata build run pipeline end to end"
spec:
  configuration: configuration.yaml
  discovery:
    exclude:
      - "templates/**"
  remotes:
    - name: infra
      type: local
      url: vendor/infra
    - name: charts
      type: helm
      url: https://charts.example.io
    - name: bundled
      type: local
      url: modules
```

## A repository your pipeline checked out

When CI clones a repository itself (for example an Azure DevOps `resources.repositories` checkout),
the pipeline has to state the ref, because it does so before strata runs. There are two ways to tell
strata where that checkout is:

- **`path`** (recommended). The pipeline owns the ref; strata only reads the folder.

  ```yaml
  remotes:
    - name: iac
      type: git
      url: https://github.com/org/iac.git
      path: ../iac             # a checkout beside the solution, in CI and on a workstation
  ```

  `path` is resolved from the solution root, not from the directory strata was started in, so the
  same `strata.yaml` means the same folder wherever it is run. Use the layout your pipeline and your
  workstations share:

  - **Beside the solution** (`../iac`): an Azure DevOps pipeline with several `checkout:` steps puts each
    repository in its own folder next to the others, which is also how developers usually clone them.
  - **Inside the solution** (`repos/iac`): exclude that folder from discovery
    (`discovery.exclude: ["repos/**"]`) so its YAML is not read as strata documents.
  - **Absolute**: used as it is. It only helps where every machine has the same layout.

  `strata validate` warns (`path_remote_outside_solution`) when the path leaves the solution, so a
  change that points a build at another folder is visible in review. When the folder is a git
  checkout, the deploy's audit manifest records its commit under
  `artifacts.repositories.<name>.commit`.
- **`reference` with `fetch: external`**. Strata expects the checkout at
  `.strata/remotes/<name>/<reference>`, so the pipeline must repeat the ref and place or link its
  checkout there.

A `path` remote cannot be pinned by a `version` document, take an `integration`, or be pushed to by a
GitOps provisioner or an audit sink: those need strata to own the clone and the ref.

## Notes

- Scope discipline: this document answers "what/where is this solution", never "what rules
  govern it" — platform policy (provider/topology registries) stays in `Configuration`.
- Remotes supply *artifacts* (Terraform modules, charts, copied files), not strata *documents* —
  a `Module`/`ProviderConfig` document must live in the solution's own discovery tree.
- No lock file: `reference` (a remote's pinned ref, see [version.md](version.md)) is declared
  here or overridden by a `version` document; the checkout location is a CLI convention, not a
  second recorded fact.
