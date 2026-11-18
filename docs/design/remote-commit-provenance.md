# Remote commit provenance - Design

- Status: current
- Last updated: 2026-11-18

## Overview

An audit must answer "which tree of each repository did this deployment use?" The deployment runs from the build
output, so only the build can answer it. `build run` therefore records two things in `resolved.yaml`: the commit of
the solution repository, and the commit of every git remote it copied files from. Each record says whether the files
taken had uncommitted changes. The deploy's audit manifest copies that record and never reads a checkout. An
opt-in option, `spec.audit.require_remote_commits`, makes a gap in the record fail the deploy.

The user-facing reference is "Which commits were deployed" in [configuration.md](../config/configuration.md). The
remote modes it covers (`path`, `fetch: external`, strata's own clone) are described in
[solution.md](../config/solution.md).

## Current Design

### The record in `resolved.yaml`

```yaml
solution:                       # the repository holding strata.yaml
  commit: 5d0e7a9c31...
  dirty: false
remotes:
  infra:
    url: https://git.example.com/org/infra.git
    ref: null                   # path mode: the pipeline owns the ref
    commit: 3f9c2e1d4b...       # full SHA, or null when it could not be read
    dirty: false                # true when files the build took had uncommitted changes
  asset:openapi:                # an asset's own spec.location
    url: https://git.example.com/org/specs.git
    ref: main                   # after version pins
    commit: 8a71b0c55e...
    dirty: false
```

- **Which remotes.** Every `type: git` remote the build took at least one file from:
  - path mode;
  - ref mode with `fetch: external`;
  - ref mode with `fetch: strata`, the clone under `.strata/remotes/<name>/<ref>`;
  - a document remote (`spec.discovery.remotes`). `build_run()` registers it, and its whole checkout counts as
    taken (`took(checkout, ".")`), so any uncommitted change there makes it dirty. Discovery's `exclude` is not
    applied to that check: at worst it reports a little more than was read.

  A remote that was resolved but gave no file is left out. A `type: local` remote is a folder of the solution, so
  `solution` covers it. `helm` and `oci` chart remotes are left out, because the manifest records each chart's name
  and version.
- **Keys.** The key is the remote's name, or `asset:<name>` for an asset's own `spec.location`. A remote name cannot
  contain `:`, so the two never collide.
- **`ref`** is the reference after version pins. It is `null` in path mode, because the pipeline chose it.
- **`solution.commit`** is `built_commit_sha()`, the value the SBOM records: a CI commit variable first, otherwise
  `git rev-parse HEAD`. A solution outside git records `commit: null` and `dirty: null` without a warning, as the
  SBOM does. That is the normal case for an example or a test.
- **`remotes`** is written as `{}` when nothing came from a remote. That tells a new build apart from one made
  before the record existed.

### Reading a checkout: `checkout_state()`

[checkout_state.py](../../src/strata/controllers/checkout_state.py) has one function,
`checkout_state(checkout, paths=None, *, exclude=(), enclosing=False) -> CheckoutState(commit, dirty, problem)`:

- **Where `.git` must be.** `.git` must be in `checkout` itself, as a folder or as a file (a worktree or a
  submodule). Otherwise `git` would walk up and report an enclosing repository's commit for a plain folder.
  `enclosing=True` lifts the rule for the solution root, which may be a folder inside its repository.
- **`commit`** is `git rev-parse HEAD`. Any failure gives `commit: None`, `dirty: None`, and the reason in `problem`.
- **`dirty`** is `git --no-optional-locks status --porcelain -- <paths> :(exclude)<exclude>...`:
  - Untracked files count, because the copy takes them. Ignored files do not, or build leftovers such as
    `.terraform/` would make every developer build dirty.
  - `--no-optional-locks` keeps `status` from rewriting the index of a checkout someone else owns.
  - Without `paths`, the dirty check is skipped.
- **Side effects.** It never raises, never writes, and never warns. The caller knows the remote's name, so it
  warns.

### Collecting what the build took: `SourceRecord`

[source_record.py](../../src/strata/controllers/source_record.py) holds `SourceRecord`, one per `build run`. It is
passed as `record=` beside `credentials=` wherever a source is resolved. Its default is `None`, which records
nothing.

- **Registering a checkout.** `source_sync._resolve_repo()` calls `record.resolved(key, remote, checkout)` for a
  `type: git` remote. Every build source passes through that one resolve call, so there is no second list of "used
  remotes" to keep in step.
- **Recording a copy.** Each place that copies calls `record.took(checkout, path, glob=...)`:

  | Copy                          | `path`                                               |
  | ----------------------------- | ---------------------------------------------------- |
  | `sync_source()`               | `source_path`                                        |
  | `sync_module_source()`        | `source_path`                                        |
  | `sync_asset()`                | the filled `source_path`, `glob=True` for a pattern  |
  | a Helm `file://` dependency   | the dependency's folder in the chart's repository    |
  | `snapshot_checkov_configs()`  | `config_file`, when it was copied                    |

  A checkout that no `resolved()` call registered is ignored: the solution root and every `type: local` remote.
- **Pathspecs.** A literal path is passed as `:(literal)<path>` and a pattern as `:(glob)<pattern>`. `:(glob)`
  matches `*` within one segment, as `pathlib.glob()` did when the files were copied. So `dirty` covers exactly the
  files taken, and a changed README elsewhere in the repository does not make a Terraform build dirty.
- **`provenance()`** runs at the end of `build_run()`, after the Checkov snapshot, which is the last copy. It runs
  `checkout_state()` once per remote, with every path taken from it.

  For the solution it uses `checkout_state(root, ["."], exclude=..., enclosing=True)`. The exclusions are
  `.strata/`, the build output when it is under the root, and every registered remote checkout under the root. A
  `repos/infra` checkout inside the solution must not make the solution dirty.
- **Writing.** `write_resolved_manifest(provenance=)` adds the two keys. `build_run()` writes `resolved.yaml` early,
  as before, and again at the end with the record. A dry run copies nothing and writes no record.

The build warns, with a hint, about each gap, whatever the option says:

- `audit_remote_commit_unavailable`: a remote's commit could not be read, for example from a plain copy;
- `remote_checkout_dirty`: the taken paths of a remote, or the solution's files, have uncommitted changes, or could
  not be checked.

### The audit manifest copies the record

`finalize_and_distribute_deploy_audit()` reads the record with `read_build_record()`.

- **What counts as a record.** It has both `solution` and `remotes`, which `build run` always writes together.
  Anything else counts as an older build:
  - a missing key;
  - a value of the wrong type;
  - an entry that `ManifestRepositoryModel` rejects.
- **`commit_sha` and `commit_dirty`** come from `solution`. They are not read again at deploy, even when a CI
  variable is set then.
- **`artifacts.repositories`** is the list of remotes the workspace names, with the record merged in:

  | Remote                                                     | Entry                                                |
  | ---------------------------------------------------------- | ---------------------------------------------------- |
  | The workspace names it, and the record has it              | The record's `{url, ref, commit, dirty}`             |
  | The workspace names it, and the build took nothing from it | `{url, ref}`, with no commit and no warning          |
  | Only the record has it                                     | The record's entry, keyed as the record keys it      |

  The entries are sorted by key. `ManifestRepositoryModel.dirty` and `DeploymentManifestModel.commit_dirty` are
  optional, so older manifests still load.
- **No checkout is read** for a build that has the record, so the deploy does not repeat the build's warnings.
- **An older build** keeps the old lookup. `_checkout_commit()` reads a path remote's `HEAD` at deploy, and warns
  when it cannot.

### `spec.audit.require_remote_commits`

`AuditConfigModel.require_remote_commits: bool = False`. With it on, `deploy_run()` calls
`check_build_record(build_path)` right after `check_build_version()`. That is offline, before the image tag
policy, before value resolution, and before any tool runs.

| Situation                           | Build (either setting)                    | Deploy, option on                       |
| ----------------------------------- | ----------------------------------------- | --------------------------------------- |
| Commit could not be read            | warning `audit_remote_commit_unavailable` | error, same code                        |
| Files taken had uncommitted changes | warning `remote_checkout_dirty`           | error, same code                        |
| `dirty` could not be read           | warning `remote_checkout_dirty`           | error `remote_checkout_dirty`           |
| Build without the record (older)    | -                                         | error `build_remote_provenance_missing` |

- **Errors.** There is one error per gap: the solution first, then each remote by key. Each error has a hint. The
  record holds no checkout path, so the hint points back to the build's warning, which names the folder.
- **What is checked.** A destroy is not checked, because it installs nothing from the build. A dry run and a smoke
  test are checked, so they show the failure early.
- **Configuration.** The option is read from the solution's one configuration. Without exactly one configuration
  it counts as off, as the other configuration-wide checks do.

## Related Decisions

- [ADR-0019](../decisions/0019-version-pinning.md) - remote pins, which decide the `ref` recorded.
- `remote-external-path.md` (work doc) - path mode, and the deploy-time commit lookup this replaced.
- `remote-commit-provenance.md` (work doc) - Parts B and C, which build on this record: committing audit files to a
  path remote, and documents in other repositories.
- `audit-trail.md` (work doc) - the audit manifest.

## History

- **The commit used to be read at deploy, from the checkout on disk.** That is wrong when build and deploy are
  separate jobs, or the checkout moved in between: the manifest named a tree the deploy never ran. So the record
  moved to the build, which is the only place that knows what the build output holds. A deploy-time comparison with
  the checkout was rejected too: the deploy never uses the checkout.
- **Only path mode used to be recorded.** A branch `reference`, whether strata fetched it or a pipeline did, gave
  no commit at all. The record now covers every git remote, and the SHA is the identity. `ref` stays as written, so
  two builds of `main` on different days differ in `commit`.
- **The recorder sits at the one resolve call, not on a list of used remotes.** The audit's old list followed only
  provisioner and module sources. Assets, Helm `file://` dependencies and Checkov configs would have been missed.
- **`:(literal)` and `:(glob)` pathspecs.** At first the paths were plain git pathspecs, where `*` also crosses `/`.
  That checked a little more than was copied. Now `:(glob)` matches as `pathlib.glob()` did.
- **The solution record does not warn outside git.** Warning there broke existing tests and would hit every
  example. A team that needs the commit turns on `require_remote_commits`, which makes the gap an error at deploy.
- **The record is merged into the manifest's list, not used in place of it.** The manifest also lists remotes the
  build took nothing from, such as a chart-based module's remote. Replacing the list would have dropped them. With
  no file taken there is no tree to record, so they get no commit.
- **Only the deploy enforces the option.** `spec.audit` is in the configuration, so the option applies on a
  developer's machine too. Failing the build would block the normal loop of editing and then building. Building is
  harmless; deploying is what the audit is about. An unreadable `dirty` counts as dirty under the option.
- **"Is the commit pushed?" is out of scope.** `git branch -r --contains` needs remote-tracking refs, which a
  pipeline's shallow, detached checkout usually lacks. `git ls-remote` needs the network and credentials, which a
  path remote never has. A recorded commit may exist nowhere else; the user docs say so.
- **`--no-optional-locks`.** Checking showed that a plain `git status` rewrites the index of a checkout it only reads.
  That checkout belongs to the pipeline.
