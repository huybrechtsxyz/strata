# Remote commit provenance - Work

- Status: draft
- Last updated: 2026-11-17

## Overview

This doc covers three things strata does with repositories that a pipeline checked out (path remotes,
`remote-external-path.md`):

- **Part A - Provenance.** An audit has to answer "which tree of each repository did this deployment use?" Today
  strata can only answer this for a `path` remote, and even there the answer can be wrong. Part A moves the record to
  the build, which is where the sources are taken. It covers every git remote, not only path mode, and adds an option
  to fail when there is no answer.
- **Part B - Committing to a path remote.** An audit sink that writes into the checkout the pipeline already made,
  instead of cloning its own.
- **Part C - Documents in other repositories.** Reading strata documents from path remotes, for example one
  repository per hub zone.

The three parts share one idea. A path remote is a checkout someone else placed. Strata may read it, record which
commit it was at, and write to it, but never clones or resets it.

**What prompted it.** Three questions from a team's configuration repository, called `platform-config` here:

- With the pipeline owning the ref, `strata.yaml` holds no ref at all. Is the deploy still auditable?
- Can strata just commit to `inventory`, which the pipeline has already checked out?
- What if each `deploy/hubs/zoneNN` folder were its own repository?

## Current Design / Progress

Phases 1 to 4 are built, which completes Part A:
- `checkout_state()`;
- the build record: `resolved.yaml` has `solution` and `remotes`;
- the audit copy: the deploy's manifest takes its commits from that record;
- `spec.audit.require_remote_commits`: with it on, `deploy run` fails on a gap in the record.

G1 to G4 are closed. G5 stays out of scope (R4). Phase 5 wrote the user-facing docs and the CHANGELOG, so Part A
is ready to release. Parts B and C (Phases 6 to 8) are not started.

The "What exists today" list below describes the state before this work. Since Phase 3 it applies only to the deploy
of a build made without the record:

- **Path mode.** `audit_run._collect_artifacts_bom()` ([audit_run.py](../../src/strata/controllers/audit_run.py))
  records each remote the workspace's provisioners and modules name as `ManifestRepositoryModel(url, ref, commit)`.
  For a `path` remote, `_checkout_commit()` fills `commit` with `git rev-parse HEAD` of the checkout. It requires
  `.git` in the folder itself. When it cannot read a commit, it warns (`audit_remote_commit_unavailable`) and
  leaves `commit` empty.
- **Every other remote** gets `commit: null`. This includes a `fetch: external` remote in ref mode (Q2 of
  `remote-external-path.md`) and a clone strata fetched itself at a branch `reference`.
- **The build** copies each source into its output (`source_sync.sync_source()`), and the deploy runs from that
  output. `resolved.yaml` recorded no remotes. The SBOM records the solution's own commit (`built_commit_sha()`) and
  nothing for remotes.
- **Pushing (Part B).** A path remote can't be pushed to.
  - `validate` reports `path_remote_not_pushable` for an audit sink or GitOps step that names one.
  - `audit_push.push_audit_files()` refuses it at run time too.
  - An audit push always works in its own clone (`layout.audit_push_checkout_path()`), which it fetches and resets
    before every write.
  - platform-config's `inventory` sink is therefore commented out. The documented workaround is a second remote with
    `reference: main` and an `integration`, which needs strata to clone from the git server in CI. It can't do that
    today.
- **Documents (Part C).** Discovery reads only the solution root, minus `spec.discovery.exclude`. Remotes supply
  sources (Terraform, charts, asset files), never documents.

### The gaps

| #   | Gap                                                                                                                                                                                                                                                    |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| G1  | **Read at the wrong time.** The commit is read when the deploy runs, from the checkout on disk. The deploy runs from the build output. When build and deploy are separate jobs, or the checkout moved after the build, the manifest names a tree the deploy never used. |
| G2  | **Uncommitted changes are invisible.** `HEAD` names a commit, not the files on disk. A build from a modified checkout records a SHA whose tree does not match what was deployed.                                                                         |
| G3  | **Only path mode is recorded.** A branch `reference`, whether strata fetched it or a pipeline did, records no commit, so "which commit of `main`?" has no answer.                                                                                         |
| G4  | **A missing commit is a warning only.** A team that needs the record cannot make its absence fail the run.                                                                                                                                              |
| G5  | **The commit may exist nowhere else.** A local commit that was never pushed is recorded as if it could be looked up.                                                                                                                                   |

## Part A - Provenance

### R1 - The build records what it took

`build run` records each git remote it resolved, at the moment it resolves it, in a new `remotes` key of
`resolved.yaml`:

```yaml
solution:                     # the repository holding strata.yaml (D13)
  commit: 5d0e7a9c31...
  dirty: false
remotes:
  infra:
    url: https://git.example.com/org/infra.git
    ref: null                 # path mode: the pipeline owns the ref
    commit: 3f9c2e1d4b...     # full SHA, or null when it could not be read
    dirty: false              # true when files the build took had uncommitted changes
  shared-modules:
    url: https://github.com/org/modules.git
    ref: v1.8.0               # after version pins
    commit: 8a71b0c55e...
    dirty: false
```

- **Where.** Every source read goes through `source_sync._resolve()` (the one `resolve_remote()` call for build
  sources). A recorder passed beside `credentials` records the remote's name and checkout there. So assets,
  module files and provisioner sources all count, with no second list of "used remotes" to keep in step.
- **Which remotes.** All `type: git` remotes: path mode, ref mode `fetch: external`, and ref mode `fetch: strata`
  (the clone under `.strata/remotes/<name>/<ref>`). A `type: local` remote is a folder of the solution, so the
  solution's commit covers it (see "The solution itself" below). `helm` and `oci` chart remotes are left out: the
  manifest already records each chart's name and version.
- **The solution itself (D13).** `resolved.yaml` also records the solution repository under `solution`:
  - `commit` comes from `built_commit_sha()`, the same value the SBOM records: a commit variable from the CI system
    first, otherwise `git rev-parse HEAD`.
  - `dirty` is `git status --porcelain` over the solution root. The build output and `.strata/` are excluded, as
    discovery excludes them. This covers the documents and every `type: local` remote.
- **How.** A helper `checkout_state(checkout, paths) -> CheckoutState(commit, dirty)` replaces `_checkout_commit()`.
  It keeps the rule that `.git` must be in the folder itself.
  - `commit` is `git rev-parse HEAD`.
  - `dirty` is `git status --porcelain -- <paths>`, limited to the `source_path`s the build took from that remote.
    Untracked files count, because the copy takes them. Ignored files do not (D11).
  - A folder that is not a git checkout gives `commit: null`, `dirty: null`, and the existing warning.
- **The SHA is the identity; `ref` stays as written.** Two builds of `main` on different days now differ in
  `commit` and diff as such.

#### `checkout_state()` (Phase 1)

[checkout_state.py](../../src/strata/controllers/checkout_state.py):

```python
@dataclass(frozen=True)
class CheckoutState:
    commit: str | None          # full SHA of HEAD
    dirty: bool | None          # None: not asked for, or could not be read
    problem: str | None = None  # why commit or dirty is None when it was asked for

def checkout_state(checkout: Path, paths: Sequence[str] | None = None, *, exclude: Sequence[str] = ()) -> CheckoutState
```

- `.git` must be in `checkout` itself, as a folder or as a file (a worktree or a submodule). Otherwise the result is
  `CheckoutState(None, None, "'<checkout>' is not a git checkout")`. Without this rule `git` would walk up and
  report the enclosing repository.
- `commit` is `git rev-parse HEAD`. A failure, such as a repository with no commit yet or one `git` refuses as
  unsafe, gives `commit: None`, `dirty: None`, and `git`'s message in `problem`.
- `paths=None` skips the dirty check, so `dirty` is `None`. Since Phase 3 the only such caller is the deploy of an
  older build, which only wants the commit.
- `paths` given: `git --no-optional-locks status --porcelain -- <paths> :(exclude)<exclude>...`, with `.` when
  `paths` is empty.
  - `--no-optional-locks` keeps `status` from rewriting the index of a checkout that someone else owns.
  - Untracked files are listed and count. Ignored files are not listed (D11).
  - Paths are git pathspecs, so a glob in an asset's `source_path` matches as `git` matches it, where `*` also
    crosses `/`. At worst it reports changes a little too broadly.
- The function never raises and never writes. Warnings are the caller's job, because only the caller knows the
  remote's name and what the record is for.

#### The build record (Phase 2)

[source_record.py](../../src/strata/controllers/source_record.py) holds `SourceRecord`, one per `build run`. It is
passed as `record=` beside `credentials=` wherever a source is resolved. Its default is `None`, which records
nothing, so every other caller is unchanged.

- **Two calls.**
  - `_resolve_repo()` calls `record.resolved(key, remote, checkout)` for a `type: git` remote. The key is the remote's
    name, or `asset:<name>` for an asset's own `spec.location`. The `:` cannot occur in a remote name, so the two
    never collide.
  - Each place that copies calls `record.took(checkout, path, glob=...)`:

    | Copy                          | `path`                                                 |
    | ----------------------------- | ------------------------------------------------------ |
    | `sync_source()`               | `source_path`                                          |
    | `sync_module_source()`        | `source_path`                                          |
    | `sync_asset()`                | the filled `source_path`, `glob=True` for a pattern    |
    | a Helm `file://` dependency   | the dependency's folder in the chart's repository      |
    | `snapshot_checkov_configs()`  | `config_file`, when it was copied                      |

    A checkout that no `resolved()` call registered is ignored. That is the solution root and every `type: local`
    remote, which the `solution` record covers.
- **Pathspecs.** A literal path is passed as `:(literal)<path>` and a pattern as `:(glob)<pattern>`. `:(glob)` matches
  `*` within one segment and `**` across segments, as `pathlib.glob()` does when the asset is copied. This replaces
  the "`*` crosses `/`" caveat of Phase 1.
- **When.** The record is read once, at the end of the build, after the policy gates (the Checkov snapshot is the
  last copy). `checkout_state()` runs once per remote, with every path taken from it. A remote that was resolved but
  gave nothing is not recorded.
- **`resolved.yaml`.** `write_resolved_manifest()` gains `provenance=`. The build writes the file early, as today,
  and again at the end with `solution` and `remotes`. `remotes` is written even when it is empty (`{}`), so Phase 3
  can tell a new build without remotes from an older build.
- **`solution`.**
  - `commit` is `built_commit_sha()`, the value the SBOM already records.
  - `dirty` is `checkout_state(root, ["."], exclude=[".strata", <build path>], enclosing=True)`. The build path is
    excluded only when it is under the root.
  - `enclosing=True` lets the solution root be a folder inside its repository. `strata.yaml` need not sit at the top
    of the repository.
- **Warnings** (R3's default column), each with a `hint`:
  - `audit_remote_commit_unavailable` when a remote's commit could not be read;
  - `remote_checkout_dirty` when the taken paths of a remote, or the solution's files, have uncommitted changes.

  A solution outside git records `commit: null` and `dirty: null` **without** a warning, as the SBOM does today. It
  is the normal case for an example or a test, and every build would otherwise warn. Phase 4's
  `require_remote_commits` makes it an error. This narrows R3's first row for the solution.

  Since Phase 3 the deploy no longer repeats these warnings for a build that has the record.
- **Dry run.** Nothing is copied, so nothing is recorded or written.

### R2 - The audit manifest copies the build's record

`audit_run` takes `repositories` from `resolved.yaml`'s `remotes` and no longer reads any checkout. This closes G1:
the manifest names the tree that went into the build output, and therefore the tree that was deployed.

- `ManifestRepositoryModel` gains `dirty: bool | None`. The field is optional and added at the end, so older manifests
  still load.
- The manifest's `commit_sha` comes from `resolved.yaml`'s `solution.commit` instead of being read at deploy. A new
  `commit_dirty: bool | None` sits beside it. An older build without `solution` keeps today's deploy-time read.
- **A build made by an older strata** has no `remotes` key in `resolved.yaml`. The audit then falls back to today's
  behaviour, so a deploy of an old build keeps its current record.

There is no deploy-time comparison with the checkout. The deploy does not read the checkout, so comparing against it
would check something the deploy never uses.

#### The audit copy (Phase 3)

`finalize_and_distribute_deploy_audit()` reads the build's `resolved.yaml` once, with `read_build_record()` (in
`source_record.py`, beside the writer).

- **A record** has both `solution` and `remotes`, which `build run` always writes together. Anything else counts as
  an older build:
  - a missing key;
  - a value of the wrong type;
  - a `remotes` entry that `ManifestRepositoryModel` rejects.

  Reading the record never fails the audit. `_platform_reference()` already reports a missing `resolved.yaml`.
- **`commit_sha` and `commit_dirty`** come from `solution`. They are not read again at deploy, even when a CI
  variable is set then, because the record holds what was built. An older build keeps `built_commit_sha()` at deploy
  time and has no `commit_dirty`.
- **`repositories`** is today's list, with the record merged in:

  | Remote                                                     | Entry                                                    |
  | ---------------------------------------------------------- | -------------------------------------------------------- |
  | The workspace names it, and the record has it              | The record's `{url, ref, commit, dirty}`                 |
  | The workspace names it, and the build took nothing from it | `{url, ref}` as today, with no commit and no warning     |
  | Only the record has it                                     | The record's entry, keyed as the record keys it          |

  - **Named but not taken.** A chart-based module's `remote` is one example. Dropping it would remove an entry today's
    manifest lists. There is no tree to record, because no file came from it.
  - **Only in the record.** Examples are an asset's `spec.location` (`asset:<name>`), a Checkov config's remote, and a
    module remote the build took files from.
  - Entries are sorted by key. The order used to follow a set's iteration order.
- **No checkout is read** for a build that has the record. So the deploy gives no `audit_remote_commit_unavailable`
  warning; the build gave it already.
- **An older build** keeps the old path exactly: `_checkout_commit()` at deploy time, with its warning.

### R3 - An option to require the record

`spec.audit.require_remote_commits` (on `AuditConfigModel`, so it sits beside the sinks it serves), default `false`
(D12). The rows below apply to the solution repository as well as to each remote.

| Situation                                    | Build (either setting)                    | Deploy, default    | Deploy, `require_remote_commits: true`          |
| -------------------------------------------- | ----------------------------------------- | ------------------ | ----------------------------------------------- |
| Commit could not be read                     | warning `audit_remote_commit_unavailable` | nothing            | error, same code                                |
| Files taken had uncommitted changes          | warning `remote_checkout_dirty`           | nothing            | error, same code                                |
| `dirty` could not be read                    | warning `remote_checkout_dirty`           | nothing            | error `remote_checkout_dirty`: unknown counts as dirty |
| Build without the record (older)             | -                                         | today's behaviour  | error `build_remote_provenance_missing`         |

The build always records and warns, and only `deploy run` fails (D14). `spec.audit` is in the configuration, so
the option applies on a developer's machine too. An error at build would block the normal loop of editing and then
building, while building is harmless and deploying is what the audit is about.

- **When the deploy checks.** It checks before it changes anything, in the deploy's preflight next to the other
  checks on the build output. It never checks in `finalize_and_distribute_deploy_audit()`, which runs after the deploy
  has happened.
- **What it reads.** It reads the same `read_build_record()` as Phase 3. Nothing is read from the checkouts.
- **The solution.** A solution that was not in git was recorded with `commit: null`, so with the option on it fails
  the deploy like a remote would. This is the R3 narrowing that Phase 2 deferred.

Each finding gets a `hint` ([diagnostic-guidance.md](diagnostic-guidance.md)), for example "Commit the change, build
again from a clean checkout, then deploy that build."

#### The deploy check (Phase 4)

- **The field.** `AuditConfigModel.require_remote_commits: bool = False`. It has no cross-field rule, so there is no
  `schema_rules` line. The schema export picks the field up from the model, and nothing is committed by hand.
- **`check_build_record(build_path) -> Diagnostics`**, in `source_record.py`, returns one error per gap:
  - no record: `build_remote_provenance_missing`, with the path of `resolved.yaml`;
  - for the solution, then each remote by key:
    - no commit: `audit_remote_commit_unavailable`;
    - otherwise, `dirty` that is not `false`: `remote_checkout_dirty`, which says whether the change was seen or
      could not be checked.

  The labels are the build's: "The solution repository", "Remote 'x'", "Asset 'x' (spec.location)".
- **Where.** `deploy_run()` runs the check right after `check_build_version()`, before the image tag policy, the
  value resolution and any tool. The option is read from the one configuration (`_require_remote_commits(index)`). It
  counts as off without exactly one configuration, as the other configuration-wide checks do. A destroy is not
  checked, because it installs nothing from the build. A dry run and a smoke test are checked, so they show the
  failure early.
- **The hint** does not name a folder, because the record holds no checkout path. The build's warning names it.


### R4 - Not in scope: "is the commit pushed?" (G5)

`git branch -r --contains <sha>` answers this only when the checkout has remote-tracking refs. A pipeline's shallow,
detached checkout usually has none, so the check would fail for the common case. `git ls-remote` would need the
network and the remote's credentials, which a path remote never has. G5 stays a known limit and is named in the
docs (Q3).

### What closes which gap

| Gap | Closed by                                            |
| --- | ---------------------------------------------------- |
| G1  | R1 + R2                                              |
| G2  | R1 (`dirty`) + R3                                    |
| G3  | R1 (every git remote)                                |
| G4  | R3                                                   |
| G5  | Not closed (R4)                                      |

## Part B - Committing to a path remote

The ask: "what if we just want to commit to that external path?" In platform-config, `inventory` is both what deployments read
(the environment inventory) and the audit sink. The pipeline has already checked it out under `repos/` and holds a
token that can push to it. Strata should write its audit files there and commit them, instead of making a second
clone it has no credentials for.

### B1 - A path remote may be a sink

An audit sink's `git.remote` may name a path remote. Strata then works in that checkout:

- It never clones, fetches over a reset, or resets. Other people's files in the checkout are not touched.
- It writes the audit files under `sink.path` as today.
- It stages and commits only the files it wrote: `git add -- <files>` and `git commit -- <files>`. Anything else
  that is modified in the tree stays uncommitted.
- The commit identity is the same as today (`_configure_identity()`, set locally in that checkout).

```yaml
# config/configuration.yaml
audit:
  sinks:
    - name: inventory
      required: false
      git:
        remote: inventory     # a path remote: repos/inventory
        branch: main        # the branch pushed to
        path: deploys
        action: push        # default; commit = commit only, none = write the files only
```

### B2 - Pushing

`action` says how far strata goes. Each step includes the ones before it.

| `action`         | What strata does                                                                                                                                                                                                                  |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `push` (default) | Writes, commits, then runs `git push origin HEAD:<branch>` with the checkout's own credentials: the pipeline's, for example Azure DevOps `persistCredentials: true` on the `checkout:` step. When the push is rejected because the branch moved, it runs `git fetch origin <branch>` and `git rebase --autostash origin/<branch>`, then retries, three times in all. |
| `commit`         | Writes and commits, then stops. A later pipeline step pushes. The manifest records the sink as "committed, not pushed".                                                                                                           |
| `none`           | Writes the files into the checkout and leaves them uncommitted. A later pipeline step commits and pushes. The manifest records the sink as "written, not committed".                                                              |

`push` is the default because a sink that does not reach the server is not durable, and durability is the point of
a sink. The other two are for a pipeline that wants to own the commit or the push.

`commit` and `none` are only valid on a path remote. A ref-mode sink writes into strata's own clone under
`.strata/audit-push/`, which no later step reads, so anything short of a push would be lost. Naming either of them
there is a schema error.

- A pipeline checkout is usually a detached `HEAD`. Committing there is fine, because the push names the branch
  (`HEAD:<branch>`). So `branch` stays required.
- A path remote has no `integration` (rule 5 of `remote-external-path.md`), so strata never adds credentials of its
  own here.
- `required` keeps its meaning. With `required: true`, a failed commit or push fails the deploy (exit 3). Otherwise
  it warns.
- Two deploys pushing at once: each writes under its own deploy path, so the rebase does not conflict. The retry
  covers the race.

### B3 - Reading and writing the same checkout

`inventory` is read by the build and written by the deploy.

- Part A records `inventory`'s commit at build time. The audit commit comes later, at deploy, and moves `HEAD`. The
  manifest still names the tree that was read, which is the point of Part A.
- The audit commit must not change what a build reads. `validate` warns (`audit_sink_overlaps_source`) when a
  sink's `path` is inside a `source_path` that a source or asset takes from the same remote.

### B4 - GitOps

A GitOps step pushing to a path remote is the same mechanism. It stays refused (`path_remote_not_pushable`) until
there is a real case (Q7). `validate` then reports that error only for GitOps.

## Part C - Documents in other repositories

The ask: in platform-config, what if each `deploy/hubs/zoneNN` folder were its own repository (`config-zone01`, `config-zone02`,
...), and strata knew to read those checkouts?

### What a zone folder holds today

- `deploy/hubs/<zone>/env-hub.yaml`: the hub environment.
- One folder per spoke, holding:
  - its deployments (`deploy-spoke.yaml`, `deploy-bootstrap.yaml`, ...);
  - its environments;
  - project folders with `deploy-customer.yaml`, environments, `params.yaml` and the occasional asset.
- They extend bases in the main repository (`extends: spoke_base`, `customer_base`) and list its
  workspaces and environments.
- Asset `source_path`s point into the main repository (`projects/<project>/...`).

So zone documents cannot stand alone: they are leaves on a shared trunk.

### First question: why split?

| Reason                                      | Needs separate repositories?                                                                                                                         |
| ------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| Different reviewers or approvers per zone   | No. `CODEOWNERS` on `deploy/hubs/zoneNN/` gives each zone its own required reviewers in one repository.                                |
| A zone team must not be able to change another zone or the shared bases | Yes, if read access must also differ, or the team must not be able to push to the main repository at all.                      |
| Separate pipelines and history per zone     | Partly. Path filters on the pipeline trigger give a pipeline per zone in one repository. Separate history needs separate repositories.                |
| Repository size                             | Not at today's size (under a hundred files under `deploy/`).                                                                                                      |

If the reason is approvals, `CODEOWNERS` is far cheaper, and nothing below is needed. The rest of Part C assumes a
reason that does need separate repositories.

### C1 - Document remotes

A solution names the path remotes whose checkouts are scanned for documents, as extra roots beside its own:

```yaml
# strata.yaml (platform-config)
spec:
  remotes:
    - name: config-zone01
      type: git
      url: https://git.example.com/org/config-zone01.git
      path: ../config-zone01
    - name: config-zone02
      type: git
      url: https://git.example.com/org/config-zone02.git
      path: ../config-zone02
  discovery:
    remotes: [config-zone01, config-zone02]   # scanned for documents like the solution root
```

The list lives on `discovery` and not as a flag on the remote. A remote says where a repository is; discovery says
what strata scans. This keeps the split `SolutionRemoteModel` already makes.

Rules:

1. **Path remotes only** at first. A ref-mode remote would need a clone before `validate` could load anything, so
   naming one is an error.
2. **One namespace.** Names are unique across all roots. A duplicate is the existing error, and the two paths show
   the repositories.
3. **Excludes.** `discovery.exclude` applies inside each root, relative to that root. A `strata.yaml` inside a
   document remote is an error: one solution, one manifest (but see C3).
4. **Paths inside documents** keep their meaning: relative to the solution root. A zone document that needs a file
   from its own repository says so with `remote: config-zone01` (assets and sources already have the field). Nothing
   is resolved relative to "the root the document came from". That rule would make a document's meaning depend on
   where it was found.
5. **A missing checkout** is an error naming the remote and its path, as `resolve_remote()` does today. A
   workstation with only some zones cloned is open question Q8.
   Decided (Q8): every remote listed in `discovery.remotes` must be checked out, or the command fails. Loading only
   what is there is a later refinement.
6. **Provenance.** Part A records every document remote's commit and `dirty` in `resolved.yaml` and the audit
   manifest, beside the source remotes. "Which commit of `config-zone01` configured this deploy?" then has an answer,
   and `require_remote_commits` covers it.
7. **Writes.** Commands that edit a document (`promote apply`, `version update`) write where the document lives,
   which can be a zone checkout. The user commits there, or Part B's mechanism does. A new document
   (`version new`) goes to the solution root unless `--path` says otherwise.
8. **Checks** see one solution. `validate`, `graph --unreferenced`, `promote status` and `deploy list` work across
   all roots, as if the zones were still folders.

### C2 - The pipeline

platform-config is the control repository. A deploy to zone01 needs only platform-config and `config-zone01`, but with every zone listed
in `discovery.remotes`, the pipeline checks out every zone, or C1 rule 5 fails it. That is the cost of one solution
with every zone in it, accepted for now (Q8).

**Later: load what is there.** Strata could load the listed remotes that are checked out and skip the rest. A
deploy would then still be checked when everything it uses is present. Any check that needs every zone, such as
`--unreferenced` or `promote status`, would report as partial or refuse to run. This needs care to stay honest, so
it waits until checking out every zone hurts.

### C3 - The other direction

Each zone repository could instead be its own solution (its own `strata.yaml`), reading the shared bases from
`platform-config` as a document remote. The same mechanism (C1) supports both directions.

|                                 | platform-config is the solution, zones are document remotes      | Each zone is a solution, platform-config is a document remote                                      |
| ------------------------------- | -------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| A zone pipeline checks out      | platform-config and every zone listed (later: only what it uses) | its zone and platform-config                                                                       |
| `validate` sees                 | everything, so a base change is checked against every zone | one zone, so a base change can break another zone unseen                                 |
| Cross-zone views                | `promote status`, `--unreferenced` and `deploy list` see everything | per zone only. `--unreferenced` would call every base another zone uses unused, unless it skips document remotes |
| Who controls the shared bases   | platform-config                                                  | platform-config, but nothing stops a zone pinning an old platform-config checkout                          |

Recommend the first direction. The second gives up the cross-zone checks, and the shared bases are where breakage
starts.

### C4 - Tooling

- `strata path get` already prints absolute paths, so it works across repositories.
- VS Code: platform-config's `.code-workspace` file already opens sibling repositories as a multi-root workspace. The
  extension's schema association and file watching must cover the document remotes too. This needs checking.

## Decisions

- **D1 - Record at build, copy at deploy.** The build output is what gets deployed, so the build is the only place
  that can say what it holds.
- **D2 - One recorder at the one resolve call.** No separate list of used remotes. The audit's own list in
  `_collect_artifacts_bom()` goes away.
- **D3 - Off by default.** Turning the option on is a decision for the team that needs the record. A warning
  without it keeps existing pipelines working.
- **D4 - `dirty` is scoped to the paths taken.** A changed README elsewhere in the repository does not make a
  Terraform build dirty.
- **D5 - A path remote is written in place, never reset.** Part B commits only the files strata wrote, in the
  checkout the pipeline made, with the pipeline's credentials.
- **D6 - Document roots are named in `discovery`, not on the remote.** A remote says where; discovery says what is
  scanned.
- **D7 - Paths in documents stay solution-relative.** A document means the same thing whichever root it was found in.
- **D8 - A path-remote sink pushes by default (Q6).** `action: none | commit | push`, default `push`. Without the
  push the audit trail is not durable.
- **D9 - Every listed document remote must be present (Q8).** platform-config is the control repository. Listing a remote
  in `discovery.remotes` means it must be checked out. Loading only what is present is for later.
- **D10 - `spec.discovery.remotes` is allowed (Q9).** It does not wait on platform-config's reason for splitting. Whether
  to split is their call, and `CODEOWNERS` stays the cheaper answer when the reason is approvals.
- **D11 - Ignored files do not make a checkout dirty (Q1).** Otherwise build leftovers such as `.terraform/` would
  make every developer build dirty.
- **D12 - The option is `spec.audit.require_remote_commits` (Q2).** It lives on the audit config because the record
  exists for the audit.
- **D13 - `resolved.yaml` records the solution's own commit (Q4).** This uses `solution: {commit, dirty}`, and it
  covers the documents and `type: local` remotes, which Part A otherwise leaves out.
- **D14 - Only the deploy enforces `require_remote_commits` (Q10).** The build always records and warns, and
  `deploy run` fails before it changes anything. A local edit-build loop keeps working with the option on.

## Open questions

- **Q1 - Do ignored files make a checkout dirty?** Decided 2026-11-17: no (D11). `sync_source()` copies them too.
  Revisit if it starts honouring `.gitignore`.
- **Q2 - Name and place of the option.** Decided 2026-11-17: `spec.audit.require_remote_commits` (D12).
- **Q3 - G5.** Leave as a documented limit, or add an opt-in `verify_pushed` that runs `git ls-remote` for remotes
  with an `integration`?
- **Q4 - The solution's own commit in `resolved.yaml`?** Decided 2026-11-17: yes, in this change, under
  `solution: {commit, dirty}` (D13).
- **Q10 - Local builds with the option on.** Decided 2026-11-17, as recommended: the build always records and warns,
  and only `deploy run` errors on a missing or dirty record (D14). The R3 table was changed to match.
- **Q5 - The SBOM.** CycloneDX can carry each remote as a component with a `vcs` external reference. Recommend:
  later, separately.
- **Q6 - Should strata push to a path remote at all, or only commit?** Decided 2026-11-17: push, with
  `action: none | commit | push` to stop earlier (D8).
- **Q7 - GitOps to a path remote.** Recommend: not until a real case. The mechanism is B1, unchanged.
- **Q8 - A checkout that is missing.** Decided 2026-11-17: every listed remote is required (D9). Loading what is
  present is the later refinement in C2. When it comes, the cross-zone checks (C1 rule 8) must say that they ran on
  a partial solution.
- **Q9 - Is Part C wanted?** Decided 2026-11-17: yes, `spec.discovery.remotes` is allowed (D10).

## Implementation Plan

Branch `feature/remote-commit-provenance`. Test-first. Each phase ends with `ruff check --fix`, `ruff format`,
`mypy src`, `lint-imports` and `pytest -q`, all green.

- [x] **Phase 1 - `checkout_state()`.** It moves out of `audit_run.py` into a controller module of its own.
  Real-`git` tests, skipped when `git` is not on `PATH`, cover:
  - a clean checkout;
  - a modified tracked file inside and outside the scoped paths;
  - an untracked file;
  - an ignored file, which is not dirty;
  - not a git checkout;
  - a plain folder inside a git repository, which must not report the parent's commit.
- [x] **Phase 2 - Build records.** Add the recorder in `source_sync`, the `remotes` key in `write_resolved_manifest()`,
  and the `remote_checkout_dirty` warning. Tests cover:
  - path mode, external ref mode and a strata clone, each recorded;
  - `local` and chart remotes, not recorded;
  - a version pin, whose ref shows after the pin;
  - a remote used by two sources, recorded once with both paths in the dirty check;
  - `solution`: its commit from a CI variable and from `git`, an edited document (dirty), and an edit under the
    build output (not dirty).

  Steps, test-first:
  1. `checkout_state(enclosing=True)`.
  2. `SourceRecord` unit tests: keys, pathspecs, one `checkout_state()` per remote, the warnings.
  3. `record=` threaded through `source_sync`, `materialise_provisioner_sources()`, `build_workload_modules()`,
     `_place_chart_dependencies()` and `snapshot_checkov_configs()`.
  4. `write_resolved_manifest(provenance=)` and the end-of-build write in `build_run()`.
  5. Real-`git` `build_run()` tests for the cases above.
- [x] **Phase 3 - Audit copies.** `_collect_artifacts_bom()` reads `resolved.yaml`, and `ManifestRepositoryModel`
  gains `dirty`. Tests cover:
  - the manifest equals the build's record after the checkout moves;
  - `commit_sha` and `commit_dirty` come from the build's `solution` record;
  - an older `resolved.yaml` falls back;
  - the manifest schema still loads an old manifest.

  Steps, test-first:
  1. `ManifestRepositoryModel.dirty` and `DeploymentManifestModel.commit_dirty`, both optional.
  2. Tests in `test_audit_run.py`. The four path-remote tests now take a provisioner source from the remote, because
     the chart-based module they used copies nothing.
  3. `_build_record()` (moved to `source_record.read_build_record()` in Phase 4); `_collect_artifacts_bom(recorded=)`;
     `commit_sha` and `commit_dirty` taken from the record.
- [x] **Phase 4 - `require_remote_commits`.** Add the schema field, the `schema_rules` text and the exported JSON
  schema. With the option on, the deploy's preflight errors on the record (D14), including a build without the
  record, and each error has a hint. Builds are unchanged. Tests cover each row of the R3 table, plus a build that
  still succeeds with the option on.

  Steps, test-first:
  1. The field on `AuditConfigModel`. No `schema_rules` line is needed: it is a plain flag.
  2. Move the reader to `source_record.read_build_record()`, so that `deploy_controller` does not import
     `audit_run`.
  3. `check_build_record()` and its unit tests, one per row.
  4. Wire it into `deploy_run()` after `check_build_version()`. Real `deploy_run()` tests cover:
     - a clean build deploys;
     - an uncommitted edit builds with a warning, then fails the deploy before any tool runs;
     - the same edit deploys with the option off;
     - an older build;
     - a solution outside git.
- [x] **Phase 5 - Docs.** Update:
  - the audit config page and the remotes config page;
  - `remote-external-path.md` (close its Q2, linking here);
  - the agent skill, if the schema field shows there;
  - CHANGELOG `[Unreleased]` as a minor release: optional fields only, plus a new warning.

  Done:
  - `config/configuration.md`: `spec.audit` in the schema list. It had no entry at all before. A new section,
    "Which commits were deployed", covers the record, the manifest copy, the warnings, the option and its three
    errors, and the limit that a commit may never have been pushed.
  - `config/solution.md`: the path-remote paragraph points there.
  - `how-to/ci-deploy-pipeline.md`: a note in "Plan in one job, apply in another".
  - The agent skill (`templates/agent/strata/SKILL.md`): a row in "where things live", and a pitfall for
    `remote_checkout_dirty`.
  - `remote-external-path.md`: Q2 closed.
  - CHANGELOG `[Unreleased]`: Added and Changed. It is a minor release: new optional fields, a new warning, an
    option that is off by default, and a manifest that now copies the build's record.
- [ ] **Phase 6 - Part B, commit to a path remote.**
  - Add `action` (`none | commit | push`, default `push`) to the git sink target. `none` and `commit` are a schema
    error on a ref-mode remote.
  - In `audit_push`, add a path-remote branch that writes in place, commits only the files written, and pushes with
    rebase and retry.
  - Narrow `path_remote_not_pushable` to GitOps, and add the `audit_sink_overlaps_source` warning.
  - Real-`git` tests against a bare repository cover:
    - `none`, which writes and leaves the files uncommitted;
    - `commit`;
    - `push`;
    - a branch that moved, where the rebase and retry succeed;
    - unrelated changes in the checkout, which stay uncommitted;
    - a detached `HEAD`;
    - a push failure with `required` on and off.
- [ ] **Phase 7 - platform-config.** A separate repository and PR. Turn the `inventory` sink on with the path remote, and set
  `persistCredentials: true` on the inventory checkout.
- [ ] **Phase 8 - Part C, document remotes.** Covers:
  - `discovery.remotes` and C1's rules;
  - a missing listed checkout, which fails naming the remote and its path (D9);
  - discovery over several roots, with the existing duplicate error;
  - document remotes in Part A's record;
  - the VS Code check (C4).

## Related Decisions

- `remote-external-path.md` (work doc): path mode, and the commit lookup this replaces.
- `audit-trail.md` (work doc): the audit manifest.
- [ADR-0019](../decisions/0019-version-pinning.md): remote pins, which decide the `ref` recorded here.

## Changelog

- 2026-11-17: Created from the question "a repo used by `path` can't be used for audit purposes". Read of
  `audit_run._collect_artifacts_bom()`, `source_sync.sync_source()` and `write_resolved_manifest()`. Nothing built.
- 2026-11-17: Added Part B (commit to a path remote) and Part C (documents in other repositories, one repository per
  hub zone). Both come from a read of platform-config: the commented-out `inventory` sink in `config/configuration.yaml`, the
  `deploy/hubs/` layout, and its `.code-workspace` file.
- 2026-11-17: Q6, Q8 and Q9 decided (D8 to D10):
  - a path-remote sink pushes by default, with `action: none | commit | push`;
  - every listed document remote must be checked out;
  - `spec.discovery.remotes` is allowed.
- 2026-11-17: Q1, Q2 and Q4 decided (D11 to D13):
  - ignored files are not dirty;
  - the option is `spec.audit.require_remote_commits`;
  - `resolved.yaml` records the solution's commit.

  Q10 was added: the option blocks a local edit-build loop.
- 2026-11-17: Phase 1 built, tests first.
  - `controllers/checkout_state.py` holds `checkout_state()` and `CheckoutState`, and has 14 real-`git` tests.
  - `audit_run._checkout_commit()` now calls it. Its behaviour is unchanged: same warning code, a slightly reworded
    message.
  - Additions to the design:
    - `paths=None` skips the dirty check;
    - `exclude` was brought forward for Phase 2's solution record;
    - a `.git` file (a worktree or submodule) counts as a checkout;
    - `--no-optional-locks`, after checking that a plain `git status` rewrites the index of a checkout it only
      reads.
- 2026-11-17: Phase 2 built, tests first. See "The build record (Phase 2)".
  - `controllers/source_record.py` holds `SourceRecord`. `record=` is passed beside `credentials=` through
    `source_sync`, `build_controller`, `workload_controller` and `checkov_controller`.
  - `checkout_state()` gained `enclosing=True` for a solution root inside its repository.
  - Literal paths use `:(literal)` and patterns use `:(glob)`, so the dirty check matches what the copy took.
  - An asset's `spec.location` is recorded as `asset:<name>`.
  - A solution outside git records nulls without a warning, which narrows R3 for the solution. Otherwise five
    existing tests, and every build outside git, would warn.
  - mypy caught a name clash: `_place_chart_dependencies()` already had a local variable `record`, so its parameter
    is `source_record`.
  - Tests:
    - `test_source_record.py` (14) and `test_build_provenance.py` (10), all against real `git`;
    - one Helm `file://` dependency test in `test_workload_controller.py`;
    - 3 more `checkout_state()` tests.
- 2026-11-17: Phase 3 built, tests first. See "The audit copy (Phase 3)".
  - The deploy's manifest copies `resolved.yaml`'s `solution` and `remotes`, and no longer reads a checkout for a
    build that has them.
  - `ManifestRepositoryModel.dirty` and `DeploymentManifestModel.commit_dirty` are new, and both optional.
  - A design point found while building it: the manifest also lists remotes the build took nothing from, such as a
    chart-based module's remote. So the record is merged into today's list rather than replacing it.
  - Tests:
    - `test_audit_run.py`: 11 tests in the provenance section. The 4 path-remote tests were reworked, and there are
      tests for a moved checkout, a dirty remote, a remote nothing was taken from, the solution's commit and
      dirty flag, and an older build (both commit and warning);
    - `test_models_audit_manifest.py`: one test that an old manifest still loads.
- 2026-11-17: Q10 decided, as recommended (D14). Only the deploy enforces `require_remote_commits`, in its
  preflight. The R3 table and Phase 4 were rewritten to match. An unreadable `dirty` counts as dirty under the
  option.
- 2026-11-17: Phase 4 built, tests first. See "The deploy check (Phase 4)". Part A is complete.
  - `spec.audit.require_remote_commits`, default `false`.
  - `source_record.check_build_record()` runs in `deploy_run()` right after `check_build_version()`.
  - The record reader moved from `audit_run` to `source_record.read_build_record()`.
  - Tests:
    - `test_source_record.py`: the reader round-trips `provenance()`, 8 malformed or older `resolved.yaml` cases,
      and one test per error row;
    - `test_audit_run.py`: 5 real `deploy_run()` tests;
    - `test_models_audit.py`: the field.
- 2026-11-17: Phase 5, the docs. Changes:
  - `configuration.md` has a new section, "Which commits were deployed";
  - `solution.md` and `ci-deploy-pipeline.md` point to it;
  - the agent skill has a new row and a new pitfall;
  - Q2 of `remote-external-path.md` is closed;
  - CHANGELOG `[Unreleased]` has entries.

  `configuration.md` had no entry for `spec.audit` at all, so it got one.
