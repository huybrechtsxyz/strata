# Remotes — Design

- Status: current
- Last updated: 2026-11-19

## Overview

A **remote** (`SolutionRemoteModel`, [solution_model.py](../../src/strata/models/solution_model.py)) gives a name to a
source that lives outside the solution's own folder: a git repository, a chart registry, or a folder. It says only
*where* the source is and how to get it. *What* to take from it is chosen where it is used, with an `@<name>/<path>`
reference (`SourceModel.remote` on a provisioner, `ModuleFileModel.source`, a document remote). Each remote is declared
once, in `strata.yaml`'s `spec.remotes`.

`strata.controllers.remote_resolution.resolve_remote()` turns a remote into a directory on disk. Every reader goes
through it: `sync_source()` for provisioner code, the workload pipeline for module files, and document discovery.

A git remote is found in one of two ways:

- **Ref mode** (`reference`): strata clones the repository at that ref, or, with `fetch: external`, expects someone else
  to have put it at `.strata/remotes/<name>/<reference>`.
- **Path mode** (`path`): a pipeline or a developer already checked the repository out, and strata reads it there. The
  pipeline owns the ref; `strata.yaml` does not repeat it.

## Current Design

### The fields

| Field         | Meaning                                                                                                                  |
| ------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `name`        | What `@<name>/...` references use.                                                                                       |
| `type`        | `git`, `oci`, `helm` or `local`.                                                                                         |
| `url`         | Where the source lives. For `local`, a path relative to the solution root. In path mode it only records which repository this is (provenance). |
| `reference`   | The ref to check out. Required for `git` (unless `path` is given) and `oci`; rejected for `helm` and `local`.            |
| `path`        | Path mode: where the checkout already is. `git` only.                                                                    |
| `fetch`       | `strata` (default): strata fetches it. `external`: CI or a developer already did, and strata only reads it.             |
| `integration` | The integration whose `authentication` supplies credentials for the fetch. It must declare the `sources` capability.    |

`type` and `fetch` are separate fields on purpose. v1 had one field for both, so a real `config/remotes.yaml` had to
call a git repository `type: bundled` just to stop v1 fetching it in a CI job with no git credentials. That lie also
turned off ref pinning and the dirty-tree check. `type: git` with `fetch: external` says the same thing honestly.

### Path mode rules

1. `type: git` states exactly one of `reference` and `path`. Both, or neither, is an error naming the remote.
2. `path` is only valid for `type: git`. A `local` remote already is a path, and `oci` and `helm` have none.
3. `path` implies `fetch: external`. An explicit `fetch: strata` with `path` is an error; `fetch: external` is allowed
   but redundant.
4. `path` is relative to the solution root (the folder holding `strata.yaml`, not the directory strata was started
   in). It may use `..` or be absolute; only an empty or option-like (`-x`) value is refused. A symlink is fine. It only
   has to be a directory when it is resolved.
5. `integration` is refused with `path`: strata never fetches a path remote, so credentials have no use.
6. `url` stays required. It is not used to find anything, but it is recorded in the audit manifest and keeps
   `strata.yaml` honest about which repository this is.

```yaml
remotes:
  - name: iac-int            # path mode: the pipeline owns the ref
    type: git
    url: https://github.example.com/org/iac-int-deployment.git
    path: ../iac-int-deployment

  - name: shared-modules     # ref mode
    type: git
    url: https://github.com/org/modules.git
    reference: v1.8.0
```

### Resolution

`resolve_remote(root, remote, ...)` returns an existing directory or raises `RemoteResolutionError`:

| Remote                                       | Result                                                                                                     |
| -------------------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `None` (unset `SourceModel.remote`)          | `root`: the solution's own repository (ADR-0018).                                                          |
| `type: local`                                | `root / url`, never copied under `.strata/remotes/`.                                                       |
| `path` set                                   | `layout.path_remote_location(root, path)`. Missing, or not a directory: an error naming the remote and the path, telling the pipeline to place the checkout there. Never created. |
| Ref mode, checkout exists                    | `layout.remote_checkout_path(root, name, reference)` (or the caller's `checkout_path`, such as an asset's `spec.location`), reused as is. |
| Ref mode, `fetch: external`, no checkout     | An error naming the exact path CI must place the checkout at.                                              |
| Ref mode, `fetch: strata`, no checkout       | `git clone` then `git checkout <reference>`. A failure removes the half-made checkout, so the next run starts clean. |
| `type: oci` or `helm`, nothing there         | An error: strata does not fetch these.                                                                     |

`RemoteResolutionError` is a `SystemError`: every case is "the environment is not set up as declared", not a bad
command or an invalid document.

**The checkout location is computed, never stored.** `remote_checkout_path()` is a pure function of the solution root,
the remote name and the ref. v1 stored a remote's location in both `config/remotes.yaml` and `.strata/solution.json`,
and builds failed with "has not been fetched yet" when the two drifted apart. Because the ref is part of the path, a
different ref gets a different directory, so a ref-mode checkout is never checked for staleness.

**Credentials.** Without `integration`, a clone uses whatever git already has (SSH agent, credential helper). With
one, `RemoteCredentialResolver` ([remote_credentials.py](../../src/strata/controllers/remote_credentials.py)) reads the
integration document's `spec.authentication`, once per build and only when a clone is actually about to run, and
`resolve_remote()` passes it to git through `git_push.credential_env()`, the same mechanism GitOps and the audit sink
use to push. There is no `SourceIntegration` class: reading `authentication` is all a fetch needs. `strata validate`
reports an unknown integration (`remote_integration_unknown`) and one without the `sources` capability
(`remote_integration_not_sources`). A private chart registry authenticates the same way
([chart-registries.md](../integrations/chart-registries.md)); Helm pulls the chart itself at deploy.

### Who uses a remote's `reference`

A path remote has none, so every user of the field either copes or refuses:

| Consumer                          | Path mode                                                                                                    |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| Version pins (ADR-0019)           | A pin on a `fetch: external` remote is already `pin_not_applicable`, which covers path mode.                 |
| Build record and audit manifest   | `ref` is empty; `commit` is the checkout's `HEAD`, recorded at build time ([remote-commit-provenance.md](remote-commit-provenance.md)). |
| `version_controller.py`           | Only reads `fetch: strata` remotes.                                                                          |
| GitOps output and the audit sink  | A path remote is written in its checkout and pushed to the configured `branch` ([remote-commit-provenance.md](remote-commit-provenance.md), "Writing to a path remote"). |

### A pipeline with path remotes

The ref of each repository lives in one place: the pipeline's checkout step. In Azure DevOps, `resources.repositories`
is resolved when the run is queued, before any checkout, so the pipeline could not read the ref from `strata.yaml`
anyway. With path mode, `strata.yaml` gives `path` and no `reference`, and the pipeline no longer has to link its
checkouts into `.strata/remotes/`.

A multi-checkout Azure DevOps job places each repository beside the others, and developers usually clone siblings
too, so `path: ../<repo>` works in both places unchanged. A checkout inside the solution (`repos/<repo>`) also works;
add it to `discovery.exclude` so strata does not load its YAML as solution documents.

The user-facing guide is [solution.md](../config/solution.md), "A repository your pipeline checked out".

### Not covered

- Fetching `oci` and `helm` remotes. Helm pulls charts itself; nothing yet needs strata to fetch a non-chart OCI
  artifact. Revisit when a real remote declares one.
- Re-fetching a branch ref. A ref-mode checkout is cloned once and never refreshed, which is right for tags and SHAs.
  A branch checkout on a long-lived workstation can go stale; delete `.strata/remotes/<name>` to refresh it. Revisit
  if that becomes a real problem.
- Checking a ref-mode `fetch: external` checkout against its `reference`. With a branch ref in a detached CI checkout,
  "at the ref" is ambiguous. The commit that was actually used is recorded instead.
- An environment override for `path`, for a layout where no single value fits both CI and workstations. No case has
  needed it.
- Making `url` optional in path mode. It is kept as provenance.

## Related Decisions

- [ADR-0018](../decisions/0018-source-model-unified-remote-reference.md) — `SourceModel`, the selection side of an
  `@<name>/<path>` reference.
- [ADR-0019](../decisions/0019-version-pinning.md) — version pins, and why ref-mode checkouts are keyed by ref.
- [ADR-0021](../decisions/0021-integration-layer.md) D9 — the `sources` capability.
- [remote-commit-provenance.md](remote-commit-provenance.md) — which commit of each remote a build used, and writing
  to a path remote.
- [asset-management.md](asset-management.md) — assets, which resolve through the same function with their own
  `spec.location`.
- [gitops-integration.md](gitops-integration.md) — pushing rendered output to a remote.

## History

- Before `resolve_remote()`, nothing turned a remote name into a directory. `sync_source()`, the workload pipeline and
  remote-hosted configuration all needed it, so it was built once at this layer instead of each inventing its own clone.
- The first draft proposed a new cache convention for checkouts. `layout.remote_checkout_path()` already existed, and a
  computed path is exactly what avoids v1's stored-location drift.
- The first draft also wanted to realign `RemoteType` with v1's `bundled`/`gitops`/`container`. `RemoteFetch`'s own
  docstring already cites the real `config/remotes.yaml` case; the split `type` + `fetch` is the deliberate correction.
- Credentials were first skipped because the one production case with private repositories used `fetch: external`.
  They were added for per-customer assets, without a `SourceIntegration` class: the integration's `authentication` is
  read directly. `fetch: external` stays the choice when CI already has the repository.
- Path mode came from a pipeline that held each ref twice (the Azure DevOps checkout and `strata.yaml`, with a comment
  saying they "must equal") and needed a script to link checkouts into `.strata/remotes/`. The pipeline is the only
  place that can own the ref, because Azure DevOps resolves it before any checkout.
- `path` is exclusive with `reference` and implies `fetch: external`: one fewer field to get wrong, and no inference
  from `fetch`.
- `path` was first limited to the solution folder, with an environment override as the escape hatch. Allowing `..` and
  absolute paths fits the sibling layout Azure DevOps and developers already share and needs no new mechanism.
- A `validate` warning for a path outside the solution shipped and was removed in the next patch. It made
  `validate --strict` fail for every pipeline using the sibling layout, and guarded nothing: writing `path` already
  says "I put it there", and the same pull request could add a script step that runs anything. Untrusted pull-request
  builds are the pipeline's concern.
- The commit lookup for a path remote requires `.git` in the folder itself, so a plain folder inside the solution's own
  repository does not report the solution's commit.
- The commit was first read at deploy time. It moved to build time, because a deploy runs from the build output and a
  deploy-time read can name a tree that was never built.
- Path remotes were first refused as GitOps and audit-sink targets, since a push needs a branch. They are now written
  in the checkout and pushed to an explicit `branch`.
