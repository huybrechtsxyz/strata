# Solution (`kind: solution`)

The bootstrap document — `strata.yaml` at the solution root. Marks "a strata solution starts
here" and supplies the minimum needed to find everything else: discovery rules and remotes live
downstream of it, never inside it. Found by walking up from any path (like `go.mod`/`package.json`),
not by discovery itself.

## Schema

- `spec.configuration` — relative path to this solution's `configuration.yaml` (adjacent by convention)
- `spec.discovery.exclude` — glob patterns excluded from document discovery, additive to the
  loader's built-in ignores (`.git`, `build`, `.strata`, tool caches). Each root applies them
  relative to itself
- `spec.discovery.remotes` — `path` remotes whose checkouts are scanned for documents too; see
  [Documents in other repositories](#documents-in-other-repositories)
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

  A path that does not exist fails `build run` with an error naming the remote and the folder it
  looked in. `build run` records the checkout's commit in `resolved.yaml`, and whether the files it
  took had uncommitted changes. The deploy's audit manifest copies both under
  `artifacts.repositories.<name>`. The folder must be a git checkout itself, not a plain folder
  inside another repository. See [Which commits were deployed](configuration.md#which-commits-were-deployed).
- **`reference` with `fetch: external`**. Strata expects the checkout at
  `.strata/remotes/<name>/<reference>`, so the pipeline must repeat the ref and place or link its
  checkout there.

A `path` remote cannot be pinned by a `version` document or take an `integration`: those need strata to
own the clone and the ref. An audit sink and a GitOps step can write to one. Strata then commits in the
pipeline's checkout and pushes with the pipeline's credentials; see
[Audit files in a repository your pipeline checked out](configuration.md#audit-files-in-a-repository-your-pipeline-checked-out).

A GitOps step (`argocd`, `flux`) on a `path` remote names the branch it pushes to, because the remote has no
`reference`:

```yaml
# workspace
provisioners:
  - name: forge_sync
    tool: argocd
    gitops:
      remote: gitops-config      # path: repos/gitops-config
      output_file: apps/forge/values.yaml
      branch: main               # the branch pushed to
      action: push               # the default; commit or none stop earlier
```

It works as the audit sink does. Strata writes `output_file` in the checkout, commits only that file, and pushes
with a rebase and retry when the branch moved. As for the sink, the pipeline must check out `branch`: commits
strata did not make are never pushed. A destroy commits and pushes the file's removal. `deploy plan`
compares with the file as the pipeline checked it out. With `action: commit` or `none` a later pipeline step
pushes, and `deploy run` reports `gitops_not_pushed` (information). `strata validate` reports:

| Finding                            | Cause                                                                   |
| ---------------------------------- | ----------------------------------------------------------------------- |
| `gitops_branch_required`           | A `path` remote with `action: push` and no `branch`                     |
| `gitops_branch_needs_path_remote`  | `branch` on a `reference` remote, which pushes to its reference         |
| `gitops_action_needs_path_remote`  | `commit` or `none` on a `reference` remote, whose clone nothing reads   |
| `gitops_output_overlaps_source` (warning) | `output_file` inside a `source_path` the build takes from that remote |

## Documents in other repositories

A solution can read documents from other repositories, for example one repository per hub zone, while the shared
bases stay in the solution. List the zone checkouts as `path` remotes and name them in `discovery.remotes`:

```yaml
spec:
  remotes:
    - name: config-zone01
      type: git
      url: https://git.example.com/org/config-zone01.git
      path: ../config-zone01        # beside the solution, in CI and on a workstation
    - name: config-zone02
      type: git
      url: https://git.example.com/org/config-zone02.git
      path: ../config-zone02
  discovery:
    remotes: [config-zone01, config-zone02]
```

Each listed checkout is scanned like the solution root:

- **One namespace.** Its documents join the solution's index, so `validate`, `graph --unreferenced`,
  `promote status` and `deploy list` see every zone at once. A zone document can `extends` a base in the solution,
  and a name defined in two roots is the usual `duplicate_document` error, naming both files.
- **Paths keep their meaning.** A path inside a document, such as an asset's `source_path`, is still relative to
  the solution root. To take a file from the zone's own repository, name it: `remote: config-zone01`.
- **`exclude`** applies inside each root, relative to that root. A folder with its own `strata.yaml` deeper in a
  checkout is a boundary, as in the solution.
- **Every listed remote must be checked out.** A pipeline that deploys one zone still checks out every zone listed,
  or every command fails with `document_remote_missing`, naming the remote and the folder. A checkout may sit
  inside the solution root (`repos/config-zone01`); it is then scanned once, as its own root.
- **Errors.** Only a `git` remote with a `path` can be listed, because documents are read before anything is
  cloned. A checkout with its own `strata.yaml` at its top fails with `document_remote_has_manifest`: it is a
  solution, not a document remote. A checkout that holds the solution root or another listed checkout fails with
  `document_remote_overlaps`.
- **Provenance.** `build run` records each listed checkout's commit in `resolved.yaml`, and the audit manifest
  copies it; see [Which commits were deployed](configuration.md#which-commits-were-deployed). The whole checkout
  counts, so any uncommitted change there makes the record `dirty`.
- **Writes.** A command that edits a document, such as `promote apply`, writes the file where it was found, which
  can be a zone checkout. Commit it there. A new document (`version new`) goes to the solution root unless you pass
  a path.
- **Path conventions** (`configuration.spec.paths`) describe the solution root's layout and do not judge documents
  found in a document remote.

In VS Code, open the solution and the zone checkouts as one multi-root workspace. `.vscode/settings.json` in the
solution maps the schemas for the solution's folder only, so map them for every folder in the `.code-workspace`
file. Check that the schema path resolves in your setup; the YAML extension's handling of a relative path there
may differ between versions:

```json
{
  "folders": [{ "path": "platform-config" }, { "path": "config-zone01" }],
  "settings": {
    "yaml.schemas": { "./platform-config/.strata/schemas/strata.json": ["**/*.yaml", "**/*.yml"] }
  }
}
```

Whether to split a solution at all is a separate question. If the reason is different reviewers per zone,
`CODEOWNERS` on each zone folder is much cheaper than separate repositories.

## Notes

- Scope discipline: this document answers "what/where is this solution", never "what rules
  govern it" — platform policy (provider/topology registries) stays in `Configuration`.
- Remotes supply *artifacts* (Terraform modules, charts, copied files), not strata *documents* —
  except a `path` remote listed in `spec.discovery.remotes`, whose checkout is a discovery root of its own.
- No lock file: `reference` (a remote's pinned ref, see [version.md](version.md)) is declared
  here or overridden by a `version` document; the checkout location is a CLI convention, not a
  second recorded fact.
