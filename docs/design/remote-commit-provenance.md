# Remote commit provenance - Design

- Status: current
- Last updated: 2026-11-19

## Overview

This doc covers three things strata does with git repositories, above all with a `path` remote: a checkout a
pipeline already made. Strata may read such a checkout, record which commit it was at, and write to it, but never
clones, fetches into or resets it.

- **Provenance.** An audit must answer "which tree of each repository did this deployment use?" The deployment
  runs from the build output, so only the build can answer it. `build run` therefore records two things in
  `resolved.yaml`: the commit of the solution repository, and the commit of every git remote it copied files from.
  Each record says whether the files taken had uncommitted changes. The deploy's audit manifest copies that record
  and never reads a checkout. An opt-in option, `spec.audit.require_remote_commits`, makes a gap in the record fail
  the deploy.
- **Writing to a path remote.** An audit sink or a GitOps step writes its files into the pipeline's checkout,
  commits only those files, and pushes with the pipeline's credentials, instead of cloning the repository again.
- **Documents in other repositories.** `spec.discovery.remotes` names path remotes whose checkouts are scanned for
  documents beside the solution root, for example one repository per hub zone.

The user-facing references are in [configuration.md](../config/configuration.md) ("Which commits were deployed",
"Audit files in a repository your pipeline checked out") and [solution.md](../config/solution.md) ("A repository
your pipeline checked out", "Documents in other repositories").

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

### Writing to a path remote

An audit sink's `git.remote` may name a path remote, and so may a GitOps step's `gitops.remote`. Strata then works
in the pipeline's checkout:

- **The checkout is not strata's.** Strata never clones, fetches over a reset, resets or switches branch in it. The
  folder must be a git checkout itself (`.git` in the folder, as for `checkout_state()`); otherwise nothing is
  written and the write fails.
- **Only its own files.** Strata writes its files, then stages and commits only those
  (`git add -A -- :(literal)<path>`, `git commit --only -- ...`). Anything else modified, staged or untracked in the
  tree stays as it was. A deleted file is committed as a removal.
- **Identity.** The commit author is the deploying actor, passed per command (`git -c user.name=... -c
  user.email=...`). The checkout's config belongs to the pipeline and is never written. Strata's own clones keep
  writing a local identity, as before. The author name is the actor as resolved; the email is built from it by
  `git_push.commit_email()`, which turns each run of characters a local part should not hold into one `.`. Azure
  Pipelines' actor is a display name, so "First Last" commits as `First.Last@strata-audit.local`, and an actor that
  is already an email (`jane@example.com`) as `jane.example.com@strata-audit.local`.
- **Pushing.** Before each push strata runs `git fetch origin <branch>`. Every commit `HEAD` has that the branch
  lacks must be strata's own (author email domain `strata-audit.local` or `strata-gitops.local`, no merge); otherwise
  nothing is pushed and the message says to check out `<branch>`. Without that check, a pipeline that checked out a
  feature branch or a pull request's merge would put that code on `<branch>`. Strata's own include a commit an
  earlier run could not push, which goes now, also when this run had nothing new to commit. When the branch moved,
  strata runs `git rebase --autostash FETCH_HEAD`, which replays only its own commits. It then runs `git push origin
  HEAD:<branch>`, so a detached checkout works, with the checkout's own credentials (for example Azure DevOps
  `persistCredentials: true`). A path remote has no `integration`. A push rejected because the branch moved again
  (`non-fast-forward` or `fetch first`) is retried, three attempts in all. Any other failure is final: an
  authentication error, or `[remote rejected]` from a protected branch or a hook. A rebase that conflicts is aborted
  (`git rebase --abort`). `--autostash` keeps the pipeline's other changes, but a staged change comes back unstaged.
- **`action`** says how far strata goes. Each step includes the ones before it:

  | `action`         | Strata                     | Then                                     | Deploy reports (information)         |
  | ---------------- | -------------------------- | ---------------------------------------- | ------------------------------------ |
  | `push` (default) | writes, commits and pushes | -                                        | -                                    |
  | `commit`         | writes and commits         | a later pipeline step pushes             | `audit_sink_not_pushed` / `gitops_not_pushed` |
  | `none`           | writes the files only      | a later pipeline step commits and pushes | `audit_sink_not_pushed` / `gitops_not_pushed` |

  `commit` and `none` need a path remote. With a `reference`, strata writes into its own clone, which nothing else
  reads, so anything short of a push is lost. The remote is in `strata.yaml` and the sink or step in another
  document, so this is a `validate` error (`audit_sink_action_needs_path_remote`,
  `gitops_action_needs_path_remote`), not a schema error. `push_audit_files()` also refuses it before running any
  `git`.

The code is one function, `integrations/git_push.commit_in_checkout()`. `controllers/audit_push.py` calls it for a
sink, and `integrations/gitops.py` for a GitOps step. It lives in the integrations layer because an integration may
not import a controller (the layer contract `lint-imports` checks). For the same reason it has its own small `checkout_problem()` check instead of
`checkout_state()`.

**An audit sink** keeps its `branch`, `path` and `required`. With `required: true`, a failed write, commit or push
fails the deploy (`audit_sink_push_failed`); otherwise it warns. Each deploy writes under its own folder, so two
deploys at once do not conflict in the rebase.

**A GitOps step** (`argocd`, `flux`) on a path remote has no `reference` to push to, so it names the branch:

```yaml
gitops:
  remote: gitops-config        # path: repos/gitops-config
  output_file: apps/forge/values.yaml
  branch: main
  action: push
```

- `branch` is required on a path remote with `action: push` (`gitops_branch_required`). On a `reference` remote it is
  an error (`gitops_branch_needs_path_remote`): the reference is the branch, and two would disagree.
- `prepare()` (at build) writes `branch` and `action` into the step's `.gitops-push.json` sidecar. A sidecar
  without them means `action: push` and no branch, which only a reference remote produced.
- Each operation works in the checkout:

  | Operation      | Path remote                                                                                    |
  | -------------- | ---------------------------------------------------------------------------------------------- |
  | `plan`         | `git diff --no-index` of the checkout's file against the rendered file                         |
  | `deploy`       | copies the rendered file to `output_file`, then `commit_in_checkout()`                         |
  | `plan_destroy` | reports whether the checkout holds `output_file`                                               |
  | `destroy`      | deletes `output_file`, then commits and pushes the removal. An untracked or absent file commits nothing |
  | `output`       | `commit_sha` is the checkout's `HEAD`; with `action: none` that commit does not hold the file  |

  `plan` compares with the tree the pipeline checked out, not the branch tip. Several stages push once each, and
  the rebase and retry covers a branch that moved in between.

**Reading and writing the same repository.** The build records a checkout's commit before the deploy commits to it,
so the record still names what was built. But a write inside a `source_path` the build takes from the same remote
would end up in the next build. `validate` warns: `audit_sink_overlaps_source` for a sink's `path`,
`gitops_output_overlaps_source` for a GitOps `output_file`. The GitOps warning also applies to a `reference`
remote, which the build reads from the branch it pushes to. Overlap means one path is inside the other, compared
by segments, so `records` and `recordsets` do not overlap. A pattern counts from its fixed folders, and `.`
overlaps everything.

### Documents in other repositories

`spec.discovery.remotes` lists path remotes whose checkouts are extra discovery roots:

```yaml
# strata.yaml
spec:
  remotes:
    - name: config-zone01
      type: git
      url: https://git.example.com/org/config-zone01.git
      path: ../config-zone01
  discovery:
    remotes: [config-zone01]
```

- **Schema.** `SolutionSpecModel` accepts only declared `type: git` remotes with a `path`, each once. A ref-mode
  remote would need a clone before `validate` could load anything.
- **One namespace.** `SolutionController.load()` walks the solution root, then each listed checkout, into one
  `DocumentIndex`. A name defined twice is the existing `duplicate_document` error, which shows both paths. So
  `validate`, `graph --unreferenced`, `promote status` and `deploy list` see every root, as if the checkouts were
  folders. `controller.document_remotes` maps each name to its checkout.
- **Excludes.** `discovery.exclude` applies inside each root, relative to that root. A checkout inside the solution
  root is skipped by the root's walk and scanned once, as its own root.
- **Paths inside documents** stay relative to the solution root. A zone document that needs a file from its own
  repository names it with `remote:`. Nothing resolves against the root a document was found in, so a document
  means the same thing wherever it lies.
- **Errors**, each naming the remote, with a hint, checked in this order:
  - `document_remote_overlaps`: the checkout holds the solution root (`path: .` or `..`), or overlaps another listed
    checkout, so documents would load twice;
  - `document_remote_missing`: the checkout is not there. Every listed remote must be checked out;
  - `document_remote_has_manifest`: a `strata.yaml` at the checkout's top. One solution has one manifest. A
    `strata.yaml` deeper inside is a boundary, as in the solution.
- **The record.** `build_run()` registers each document remote with `SourceRecord`, with `took(checkout, ".")`, so
  `resolved.yaml`, the audit manifest and `require_remote_commits` cover it. Any uncommitted change in the checkout
  makes it dirty. Discovery's `exclude` is not applied to that check: at worst it reports a little more than was
  read.
- **Writes.** `promote apply` and `version update` write to `IndexEntry.source`, wherever the document was found. A
  new document (`version new`) goes to the solution root unless `--path` says otherwise.
- **Code that assumed one root.** Path conventions judge only documents under the solution root. `version show`
  prints paths with `layout.display_path()`.
- **Editors.** The VS Code extension resolves each finding's absolute `source`, so findings in a zone checkout
  land on the right file. Schema association for a zone folder needs a `yaml.schemas` entry in the
  `.code-workspace` file; `solution.md` shows it.

### Not covered

- **Loading only the listed checkouts that are present.** Every listed remote must be checked out, so a pipeline
  for one zone checks out every zone. Loading what is there would need every cross-root check (`--unreferenced`,
  `promote status`) to say that it ran on part of the solution. Revisit when checking out every zone hurts.
- **"Is the commit pushed?"** See History.
- **The SBOM.** `sbom.json` lists the solution's commit but not the remotes. CycloneDX could carry each one as a
  component with a `vcs` reference, but that changes what scanners read, so it is its own change, when someone
  needs the SBOM alone to say which code went in.

## Related Decisions

- [ADR-0019](../decisions/0019-version-pinning.md) - remote pins, which decide the `ref` recorded.
- [gitops-integration.md](gitops-integration.md) - the GitOps integration, including its own clone for a `reference`
  remote.
- [remotes.md](remotes.md) - remotes, path mode, and how `resolve_remote()` finds a checkout.
- [audit-trail.md](audit-trail.md) - the audit manifest and its sinks.

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
  path remote never has. A CI build's commits come from the server anyway. A recorded commit may exist nowhere
  else; the user docs say so.
- **`--no-optional-locks`.** Checking showed that a plain `git status` rewrites the index of a checkout it only reads.
  That checkout belongs to the pipeline.
- **A path remote used to be read-only.** `validate` refused it as an audit sink or GitOps target
  (`path_remote_not_pushable`), and the workaround was a second remote with a `reference` and an `integration`, so
  strata cloned in CI with credentials it often did not have. Writing in the pipeline's checkout removes the second
  clone and the second credential.
- **A path-remote write pushes by default.** A sink that never reaches the server is not durable, which is the point
  of a sink. `commit` and `none` exist for a pipeline that wants to own the commit or the push.
- **"Not pushed" is information at deploy, not a manifest field.** The manifest is one of the files being written,
  so it is complete before the sink runs and cannot record the sink's own outcome.
- **Only a "branch moved" rejection retries, and the rebase uses `FETCH_HEAD`.** An authentication or network
  failure will not fix itself, and neither will a protected branch: the first version also retried on any
  `rejected`, which matched `[remote rejected]` and rebased the checkout three times for nothing. A pipeline's
  single-branch or shallow clone may not update `origin/<branch>`, and the rebase works on a `--depth 1` checkout
  too.
- **Only strata's commits are pushed.** `HEAD:<branch>` pushes everything `HEAD` holds, so the first version would
  have put a feature branch on `main` when the pipeline checked one out. The check is by author email domain, which
  only strata's commits use; it also lets a commit an earlier run left unpushed go with the next run.
- **GitOps waited for a real case, then reused the sink's code.** The first version refused GitOps on a path
  remote. When it was built, the commit-and-push tail moved from `audit_push` to `git_push.commit_in_checkout()`, so
  one function serves both. A GitOps `branch` is required only for a push, and is an error on a `reference` remote
  rather than an override of it.
- **Document roots are named in `discovery`, not on the remote.** A remote says where a repository is; discovery
  says what strata scans.
- **Why split documents across repositories at all.** For different reviewers per zone, `CODEOWNERS` on each zone
  folder is far cheaper. Separate repositories are for different read access, separate history, or a team that must
  not push to the main repository. Strata supports the split, but the choice stays with the team.
- **The solution holds the zones, not the other way round.** Each zone could have been its own solution that reads
  the shared bases as a document remote. That gives up `validate` across zones, so a base change could break
  another zone unseen, and `--unreferenced` would call a base unused that another zone uses. The shared bases are
  where breakage starts, so the solution that holds them sees every zone.
- **Documents found outside the solution root.** Three places assumed every document sat under the root and would
  have raised on a zone document: both path-convention checks and `version show`. They were fixed with the feature,
  with a regression test.
