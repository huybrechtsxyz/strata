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

**Part A is built and documented.** The finished design is
[remote-commit-provenance.md](../design/remote-commit-provenance.md) (design doc), and the user reference is "Which
commits were deployed" in [configuration.md](../config/configuration.md). In short:

- `build run` records the commit of the solution, and of each git remote it took files from, in `resolved.yaml`;
- the deploy's audit manifest copies that record;
- `spec.audit.require_remote_commits` makes a gap in the record fail the deploy.

**Part B's strata side is built (Phase 6).** An audit sink may write to a path remote: strata commits in the
pipeline's checkout and pushes with its credentials. `git.action` (`push | commit | none`) stops earlier. Phase 7,
turning platform-config's `inventory` sink on, is next and lives in that repository.

Before Phase 6, a path remote could not be pushed to. `validate` refused an audit sink or a GitOps step that named
one, and platform-config's `inventory` sink was commented out. The documented workaround was a second remote with
`reference: main` and an `integration`, which needs strata to clone from the git server in CI. A GitOps step is
still refused (B4).

**Part C is built (Phase 8).** `spec.discovery.remotes` lists path remotes whose checkouts are scanned for
documents, and the build records each one's commit. Loading only the checkouts that are present (C2, "later") is not
built: every listed remote must be checked out (D9).

Before Phase 8:
- **Documents (Part C).** Discovery reads only the solution root, minus `spec.discovery.exclude`. Remotes supply
  sources (Terraform, charts, asset files), never documents.

## Part A - Provenance

Built. See the design doc. It closed these gaps:

| #   | Gap                                                                                       | Closed by                                       |
| --- | ----------------------------------------------------------------------------------------- | ----------------------------------------------- |
| G1  | The commit was read at deploy, from a checkout the deploy never used.                     | The build records it; the manifest copies it.   |
| G2  | Uncommitted changes were invisible.                                                       | `dirty`, and the option.                        |
| G3  | Only path mode was recorded.                                                              | Every git remote is recorded.                   |
| G4  | A missing commit was a warning only.                                                      | `require_remote_commits`.                       |
| G5  | The commit may exist nowhere else.                                                        | Not closed: out of scope (Q3).                  |

Parts B and C build on the record. An audit commit to a path remote moves its `HEAD` after the build has recorded
it (B3), and documents read from a remote are recorded the same way (C1).

## Part B - Committing to a path remote

The ask: "what if we just want to commit to that external path?" In platform-config, `inventory` is both what deployments read
(the environment inventory) and the audit sink. The pipeline has already checked it out under `repos/` and holds a
token that can push to it. Strata should write its audit files there and commit them, instead of making a second
clone it has no credentials for.

### B1 - A path remote may be a sink

An audit sink's `git.remote` may name a path remote. Strata then works in that checkout:

- It never clones, fetches over a reset, or resets. Other people's files in the checkout are not touched.
- It writes the audit files under `sink.path` as today.
- It stages and commits only the files it wrote: `git add -- <files>` and `git commit --only -- <files>`, as
  `:(literal)` pathspecs. Anything else that is modified, staged or untracked in the tree stays as it was.
- The commit identity is the deploying actor, as before, but it is passed per command
  (`git -c user.name=... -c user.email=...`). It is not written to the checkout's config, which belongs to the
  pipeline. The ref-mode clone keeps `_configure_identity()`.
- The folder must be a git checkout itself (`checkout_state()`). Otherwise nothing is written and the sink fails.

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
| `push` (default) | Writes, commits, then runs `git push origin HEAD:<branch>` with the checkout's own credentials: the pipeline's, for example Azure DevOps `persistCredentials: true` on the `checkout:` step. When the push is rejected because the branch moved, it runs `git fetch origin <branch>` and `git rebase --autostash FETCH_HEAD`, then retries, three times in all. |
| `commit`         | Writes and commits, then stops. A later pipeline step pushes. The deploy reports `audit_sink_not_pushed` (information).                                                                                                            |
| `none`           | Writes the files into the checkout and leaves them uncommitted. A later pipeline step commits and pushes. The deploy reports `audit_sink_not_pushed` (information).                                                               |

`push` is the default because a sink that does not reach the server is not durable, and durability is the point of
a sink. The other two are for a pipeline that wants to own the commit or the push.

The manifest cannot record "committed, not pushed": it is one of the files being written, so it is complete before
the sink runs. So the deploy reports the state as information instead.

`commit` and `none` are only valid on a path remote. A ref-mode sink writes into strata's own clone under
`.strata/audit-push/`, which no later step reads, so anything short of a push would be lost. That cannot be a schema
error, because the sink is in the configuration and the remote is in `strata.yaml`. So `validate` reports it
(`audit_sink_action_needs_path_remote`, with a hint), and `push_audit_files()` refuses it before running any `git`.

- **Which push failures retry.** Only a rejected push retries: one whose stderr says `rejected`, `non-fast-forward`
  or `fetch first`. An authentication or network failure fails at once.
- **A rebase that conflicts** is aborted (`git rebase --abort`), so the checkout is left with no rebase in progress.
- **`FETCH_HEAD`, not `origin/<branch>`.** A pipeline's single-branch or shallow clone may not update the
  remote-tracking ref. The rebase works on a shallow (`--depth 1`) checkout too. A test covers it.
- **`--autostash`** restores the pipeline's other changes after the rebase, but a staged change comes back
  unstaged. The user docs say so.

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
  sink's `path` overlaps a `source_path` that a provisioner, module or asset takes from the same remote. Overlap
  means one path is inside the other, compared by segments, so `records` and `recordsets` do not overlap. A
  pattern counts from its fixed folders, and `.` overlaps everything.

### B4 - GitOps

A GitOps step pushing to a path remote is the same mechanism. It stays refused (`path_remote_not_pushable`) until
there is a real case (Q7). Since Phase 6, `validate` reports that error only for GitOps.

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

- [x] **Phases 1 to 5 - Part A.** `checkout_state()`, the build record, the audit copy, `require_remote_commits`, and the docs.
  Built and documented; see the design doc and the Changelog below.
- [x] **Phase 6 - Part B, commit to a path remote.**
  - Add `action` (`none | commit | push`, default `push`) to the git sink target. `none` and `commit` on a ref-mode
    remote are a `validate` error (`audit_sink_action_needs_path_remote`) and a run-time refusal. They cannot be a
    schema error, because the remote is in another document.
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

  Done, test-first:
  - `test_audit_push_path_remote.py` has 12 tests against a bare repository and a pipeline-style detached clone:
    - `push`, `commit` and `none`;
    - only the written files are committed, with modified, staged and untracked files left alone;
    - a moved branch, also with a shallow clone;
    - a rebase conflict, aborted;
    - a push that keeps failing;
    - nothing to commit;
    - the checkout's config is left unchanged;
    - a folder that is not a checkout;
    - `commit` and `none` on a ref-mode remote, refused before any `git` runs.
  - `test_audit_run.py` has 3 end-to-end deploy-audit tests: a pushed sink, `commit` with its information finding,
    and a required sink whose push fails.
  - `test_semantic_checks.py` covers GitOps still refused, a sink allowed, `action` on each remote kind, and overlap
    cases: inside, `.`, a pattern, a sibling with a shared prefix, another remote.
  - `test_models_audit.py` covers the field.
  - The docs:
    - `configuration.md` has a new section, "Audit files in a repository your pipeline checked out";
    - `solution.md` and the agent skill drop "a path remote cannot be pushed to by an audit sink";
    - CHANGELOG `[Unreleased]` has entries.
  - `required` on and off reuses the existing `audit_sink_push_failed` path. It is tested with a real failing push
    (required on) and with the existing mocked tests (both).
- [ ] **Phase 7 - platform-config.** A separate repository and PR. Turn the `inventory` sink on with the path remote, and set
  `persistCredentials: true` on the inventory checkout.
- [x] **Phase 8 - Part C, document remotes.** Covers:
  - `discovery.remotes` and C1's rules;
  - a missing listed checkout, which fails naming the remote and its path (D9);
  - discovery over several roots, with the existing duplicate error;
  - document remotes in Part A's record;
  - the VS Code check (C4).

  Done, test-first (`test_document_remotes.py`, 20 tests, the build-record ones against real `git`):
  - **Schema.** `SolutionDiscoveryModel.remotes`. `SolutionSpecModel` rejects a name that is not a declared `git`
    remote with a `path`, and a name listed twice (rule 1).
  - **Discovery.**
    - `SolutionController` walks the solution root and then each listed checkout. One `DocumentIndex` means one
      namespace and the existing `duplicate_document` error (rules 2 and 8).
    - `exclude` applies relative to each root (rule 3). A checkout inside the solution root is skipped by the root's
      walk and scanned once, as its own root.
    - `controller.document_remotes` maps each scanned name to its checkout.
  - **Errors**, each naming the remote, with a hint:
    - `document_remote_missing` (rule 5, D9);
    - `document_remote_has_manifest`: a `strata.yaml` at the checkout's top (rule 3). A nested one deeper is a
      boundary, as in the solution;
    - `document_remote_overlaps`: a checkout holding the solution root (`path: .` or `..`), or overlapping another
      listed checkout, whose documents would load twice.
  - **The record** (rule 6). `build_run()` registers each document remote in `SourceRecord`, with `took(checkout,
    ".")`. So `resolved.yaml`, the audit manifest and `require_remote_commits` cover it. The solution's dirty check
    already excludes registered checkouts under the root.
  - **Writes** (rule 7) needed no change: `promote apply` and `version update` write to `IndexEntry.source`, which
    is wherever the document was found.
  - **Found while building it.** Three places assumed every document sits under the solution root, and would raise
    `ValueError` on a zone document: both path-convention checks in `semantic_checks`, and `strata version show`.
    Path conventions now judge only documents under the solution root, which is documented. `version show` uses
    `layout.display_path()`. A regression test fails without the fix.
  - **C4, VS Code.**
    - The extension runs `strata validate` from the first workspace folder, and resolves each finding's `source`
      with `path.resolve(root, source)`. An absolute path from a zone checkout therefore lands on the right file.
      No change needed.
    - Schema association does need the user to act. The scaffolded `.vscode/settings.json` maps
      `.strata/schemas/strata.json` for the solution's folder only, so a zone folder in a multi-root workspace gets
      no schema. `solution.md` shows a `yaml.schemas` entry in the `.code-workspace` file. Whether the YAML extension
      resolves that relative path as shown is not verified (a follow-up).
  - **Docs:**
    - `solution.md` has a schema line and a new section, "Documents in other repositories";
    - `configuration.md`'s record section mentions document remotes;
    - the agent skill has a new row;
    - CHANGELOG `[Unreleased]` has an entry.

## Related Decisions

- [remote-commit-provenance.md](../design/remote-commit-provenance.md) (design doc): Part A, finished.
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
- 2026-11-18: Part A graduated to `docs/design/remote-commit-provenance.md`. Its sections here were collapsed to a
  summary and a gap table, and Phases 1 to 5 to one line.
- 2026-11-18: Phase 6 built, tests first: an audit sink on a path remote. Changes to the design:
  - the commit identity is passed per command, not written to the pipeline checkout's config;
  - only a rejected push retries;
  - the rebase uses `FETCH_HEAD`;
  - `commit` and `none` on a ref-mode remote are a `validate` error, not a schema error;
  - "not pushed" is information at deploy, not a manifest field (the manifest is one of the files written).
  - The `## Notes` heading of `configuration.md` had gone missing in the Part A commit, so it was restored.
- 2026-11-18: Phase 8 built, tests first: document remotes (`spec.discovery.remotes`). Changes to the design:
  - a checkout inside the solution root is allowed, and scanned once;
  - `path: .` or `..`, and nested listed checkouts, are errors (`document_remote_overlaps`);
  - a `strata.yaml` deeper inside a checkout is a boundary, not an error;
  - path conventions skip documents outside the solution root;
  - the dirty check takes the whole checkout and ignores discovery's `exclude`.

  Three `relative_to(root)` calls that would have raised on a zone document were fixed. The VS Code extension needs
  no change; schema association needs a `.code-workspace` setting, which is documented but not verified.
