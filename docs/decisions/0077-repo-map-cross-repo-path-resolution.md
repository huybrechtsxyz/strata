# Repo Map & Cross-Repo Path Resolution — Current State, Issues, and Redesign

- Status: proposed — two tracks; Track 1 (non-breaking) ready to implement, Track 2 (breaking) awaiting sign-off
- Date: 2026-09-11
- Related: [ADR 0010 — Rename configuration spec.repositories to spec.remotes](./0010-rename-configuration-repositories-to-remotes.md), [ADR 0072 — Clarifying spec.layers vs spec.paths](./0072-clarify-layering-vs-path-convention.md)

## Context and Problem Statement

Strata needs to answer one recurring question throughout the codebase: **"given a
repo/remote name, where is its content on disk right now?"** — so that
`@repo_name/relative/path` references in YAML, and provisioner `source.repository`
values, resolve to real files.

ADR-0010 already established that there are two conceptually distinct registries
that both used to be (confusingly) named "repositories," and deliberately kept them
separate:

1. **`solution.json → spec.repositories`** (`SolutionController`) — developer/machine-local
   state, managed by `strata repo add`/`sync`. "Where is my source code checked out?"
2. **`config/*.yaml → spec.remotes`** (`RemoteModel`) — team-shared, hand-authored
   config. "What remote endpoints does the platform pull from / push artifacts to?"

That separation was and is the right call — the two answer genuinely different
questions and have different ownership/lifecycle (ADR-0010's own reasoning still
holds). **This ADR is not about undoing that split.** It exists because, in practice,
one specific field on the "shared config" side — `RemoteModel.deploy_path` — has
been pressed into service as a *third, informal* answer to the *same* checkout-path
question `solution.json` already owns, and nothing in the codebase stops that, checks
for disagreement between the two, or even consistently asks the same registry twice
in a row. The result: **the same real-world question ("where is repo X?") currently
has up to three different candidate answers, no reconciliation, and no single
command that consults all of them the same way.**

## The three answers to "where is repo X?"

| #   | Source                                                                                                                                                             | Who writes it                               | Intended question                                                                                                          | Committed to git?              | Actually machine-independent?             |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- | ------------------------------ | ----------------------------------------- |
| 1   | `.strata/solution.json` → `spec.repositories[].path`/`.url`                                                                                                        | `strata repo add`/`sync` (CLI, per-machine) | "Where did *I* clone/point this repo?"                                                                                     | **No — by design** (see below) | No — this is the whole point              |
| 2   | `config/*.yaml` (e.g. `remotes.yaml`) → `spec.remotes[].deploy_path` (`RemoteModel`)                                                                               | hand-authored, team-committed               | Per ADR-0010/[docs/config/manifest.md](../config/manifest.md#L90): target *inside* a remote for GitOps manifest publishing | Yes                            | Yes (that's what makes it safe to commit) |
| 3   | Whatever the environment's own bootstrap step actually did (a CI `checkout:` step's `path:`, a developer's `git clone` location, `docker-compose` bind mount, ...) | the environment itself                      | The actual ground truth, right now, on this machine                                                                        | N/A                            | No                                        |

Row 2 is where the trouble starts: nothing in `RemoteModel`, its docstring, or any
validator prevents (or even warns about) `deploy_path` being read as "the local
checkout path" instead of its documented "manifest publish target" meaning — and at
least one real deployment repository does exactly that (see below).

> **Authoring-surface note.** `spec.remotes` is authored in the workspace's
> hand-written `config/*.yaml` files (e.g. `remotes.yaml`), which *are* committed.
> `.strata/configuration.yaml` is a **generated, gitignored merge** of those
> sources — rewritten by the CLI on every run with comments stripped and keys
> reordered. Any discussion of "the committed config" in this ADR means the
> `config/*.yaml` sources, never the generated merge.

## Concrete example — a real strata-managed deployment repository (anonymized)

`config/remotes.yaml` (in a real strata-managed deployment repository — not part
of this codebase; names below are anonymized) carries this comment, verbatim,
next to its `infra-remote`/`env-remote` remote declarations:

> *"`deploy_path` is the directory, relative to the workspace root, where this
> remote's working copy lives. It is not optional in practice... These values must
> match the `path` recorded for the same repository in `.strata/solution.json`,
> which is what `strata repo add` wrote."*

That is a hand-maintained invariant between two independently-editable files,
enforced by nothing. And it's not just theoretical drift — the actual repository
has all three rows disagreeing simultaneously:

- **Row 2** (`config/remotes.yaml`, committed): `deploy_path: "../infra-repo"`
  — a sibling-folder convention that only makes sense for a developer's local
  checkout layout.
- **Row 1** (`.strata/solution.json`, tracked in git — see next section): also
  `"../infra-repo"` today, but only because it happens to have been
  committed from the same developer's machine that authored row 2. There is no
  mechanism keeping them equal; they simply haven't drifted apart *yet* on this
  particular repo.
- **Row 3** (the actual CI checkout, per this same repo's
  `.azure/pipelines/azure-pipelines.yml` `resources.repositories` + `checkout:`
  step): `$(Pipeline.Workspace)/s/repos/infra-repo` — nested under the
  pipeline workspace. **Neither row 1 nor row 2's committed value matches this at
  all**, and the pipeline never calls `strata repo add` to reconcile them (verified
  — no such call exists anywhere in `.azure/`).

### The git-tracking leak (verified)

Strata's own shipped scaffold ([`dot.strata/dot.gitignore`](../../src/strata/templates/solution/dot.strata/dot.gitignore#L8))
explicitly lists `solution.json` as never-commit — the tool's own stated intent is
unambiguous: this file is machine-local, generated state. That deployment repo's own
`.strata/.gitignore` **already has this exact rule**. And yet:

```
git log --diff-filter=A -- .strata/solution.json
→ "Initial solution creation"   (the very first commit)
```

`.gitignore` rules are not retroactive — a file already tracked stays tracked
until explicitly `git rm --cached`. So `solution.json` has been committed,
shared, and pulled by every clone of this repo since day one, despite the
project's own convention saying it never should have been. `git status --short`
on a real working copy shows it as locally modified (`M .strata/solution.json`)
essentially permanently, because every developer's real checkout paths differ
from whatever was last committed.

### One registry entry, two different resolved paths (verified — strongest exhibit)

The two-registry disagreement above needs two files to demonstrate. This one needs
neither — **a single entry in a single file already resolves to two different
absolute paths depending on which code path asks.**

Given one ordinary `solution.json` entry for the workspace's own config directory:

```json
{ "name": "config", "url": ".", "path": "config", "type": "local" }
```

- [`StatusRepoSolutionCommand._execute()`](../../src/strata/commands/repo/status_repo_solution_command.py#L74)
  resolves `Path(repo.path)` against `self._work_path` → **`<work_path>/config`**
- [`SolutionController.get_repo_map()`](../../src/strata/controllers/solution_controller.py#L444)
  resolves `Path(r.url)` against `os.getcwd()` → **the workspace root itself**

That is one entry, one file, two answers — and the divergence is doubled, because
the two code paths disagree on **both** inputs:

|                                  | Field consulted | Base path         |
| -------------------------------- | --------------- | ----------------- |
| `strata repo status`             | `path`          | `self._work_path` |
| `get_repo_map()`, `type: local`  | `url`           | `os.getcwd()`     |
| `get_repo_map()`, `type: gitops` | `path`          | `self._work_path` |

Note the third row: `get_repo_map()` is internally inconsistent with *itself* —
`local` repos are keyed off `url` relative to the process working directory, while
`gitops` repos are keyed off `path` relative to the workspace root. So the answer
to "where is repo X?" for a `local` repo also changes depending on **which
directory the user happened to run `strata` from**.

### This is not merely inconsistent — it is a live, reproducible failure

The `os.getcwd()` branch is not a latent tidiness issue. It is a user-visible bug
today, reproducible as a controlled experiment: **identical command, identical
explicit `--work-path`, identical work-path-relative `-f`, with CWD as the only
variable.** Run from the workspace root it succeeds; run from a nested deployment
directory it fails.

The mechanism, for a workspace whose active profile references `@config/config/base.yaml`
where `config` is `type: local` with `url: "."`:

| CWD at invocation    | `repo_map["config"]` resolves to | `@config/config/base.yaml` resolves to                             |
| -------------------- | -------------------------------- | ------------------------------------------------------------------ |
| workspace root       | `<workspace>`                    | `<workspace>/config/base.yaml` ✅ exists                            |
| `deploy/control/dev` | `<workspace>/deploy/control/dev` | `<workspace>/deploy/control/dev/config/base.yaml` ❌ does not exist |

Three things make this the strongest exhibit in this ADR:

1. **An explicitly-supplied correct answer is silently ignored.** The user passed
   `--work-path` pointing at the right directory. `SolutionController` *has*
   `self._work_path` — it is used on the very next line for `gitops` repos — and
   the `local` branch reaches past it to `os.getcwd()` anyway. Giving strata the
   right answer does not save you.
2. **The diagnostic names the wrong thing.** The failure surfaces as a complaint
   about the profile's config refs. The refs are fine. The user is sent to
   investigate a file that is correct, for a problem caused somewhere else
   entirely — the same misdiagnosis cost that made the `build plan` bug expensive.
3. **It means running `strata` from a subdirectory is currently unsupported** in
   any workspace that combines `type: local` repos with `@ref` references — and
   this is documented nowhere, warned about nowhere, and validated nowhere.

The fix is a one-line change (`os.getcwd()` → `self._work_path`), is
non-breaking, and does not depend on any schema decision in this ADR. It belongs
in Track 1.

This is the real thesis of this ADR, demonstrated without needing the
two-registry argument at all: **path resolution logic is duplicated across call
sites rather than centralised, the copies have already drifted, and the drift is
reaching users.**

## Two independent problems (do not conflate)

The originally-reported `strata build plan` failure is evidence for this ADR, but
it is important to be precise about *which* problem it is evidence for — because
the two are separable, and one of them is fixable today without any schema change.

**Problem A — two registries hold the same fact, unsynchronised.**
Schema-level. Needs the redesign in Option F. This is what the `deploy_path`
vs `solution.json` disagreement and the git-tracking leak are evidence for.

**Problem B — location resolution is an optional parameter with a silent default.**
Code-level. Nothing to do with how many registries exist. The mechanism is:

```python
# builders/*_builder.py::_copy_provisioner_source()
if repo_map and repo_name and repo_name in repo_map:
    repo_root = Path(repo_map[repo_name])
else:
    repo_root = work_path          # silent fallback — no error, no warning
```

… combined with `repo_map: Optional[Dict[str, str]] = None` being an **optional
keyword argument threaded manually through call signatures**. `PlanBuildCommand`
simply forgot to pass it, received `{}`, and fell through to `work_path`. The
resulting error ("Terraform source directory not found: `<work_path>/<source_path>`")
is indistinguishable from a genuine repository misconfiguration, which is where
the real cost of this bug came from — hours of misdiagnosis, not the wrong path
itself.

**This would have happened identically with one registry, or with three.**

The distinction matters for two reasons:

1. **Problem B is fixable now, non-breaking** — remove the `work_path` fallback so
   an unresolvable repo name is a hard error naming the repo, and make resolution
   a required dependency (injected resolver or service lookup) rather than an
   optional kwarg. That is a patch release, not a schema migration.
2. **Option F's "one resolution path, so nothing for a new command to forget"
   claim only holds if resolution stops being *passed in*.** If a future unified
   resolver is still handed to builders as an optional dict argument, the next new
   `strata build <thing>` forgets it in exactly the same way and gets exactly the
   same silent fallback. Shipping the breaking change without fixing B would keep
   the bug class alive.

## Where different commands consult which answer (verified by code reading)

| Consumer                                                                                           | Reads row 1 (`solution.json`)                                                           | Reads row 2 (`remotes.yaml`/`get_remote_map()`)                                                                      | Notes                                                                             |
| -------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `TerraformBuilder`/`AnsibleBuilder`/`BicepBuilder`/`HelmBuilder`._copy_provisioner_source()_       | ✅ only                                                                                  | ❌ never                                                                                                              | `repo_map` param, direct `repo_name in repo_map` lookup — no `@` syntax           |
| `WorkspaceService`/`NamespaceService`/`CustomerService`/`DeploymentService` (via `resolve_path()`) | ✅                                                                                       | ✅ (merged, **row 1 wins**: `{**config_repo_map, **(repo_map or {})}`, duplicated near-verbatim in all four services) | Only path for `@repo_name/...` references in YAML fields                          |
| `RunBuildCommand._execute_terraform_build/_ansible_build/_bicep_build/_helm_build()`               | ✅ (computed once per builder call, passed through)                                      | ❌ never reaches the builder                                                                                          | Correct today, but only reads row 1                                               |
| `PlanBuildCommand._build_to_temp()`                                                                | ✅ **now** (fixed 2026-09-11 — previously omitted entirely, silently defaulting to `{}`) | ❌ never                                                                                                              | Was the reported bug; now matches `RunBuildCommand`'s (still row-1-only) behavior |
| `strata repo status`/`strata repo list`                                                            | ✅ (that's their whole purpose)                                                          | ❌                                                                                                                    | Reports on row 1 only — has no way to flag disagreement with row 2                |

No command today cross-checks rows 1 and 2 for the same name and warns on
disagreement. No command re-derives row 1 or row 2 from row 3 (the actual
environment) automatically.

## What is missing

1. **A single, un-duplicated merge function.** The `{**config_repo_map, **(repo_map or {})}`
   pattern is hand-copied into four separate service classes and *absent* from the
   two build-command entry points that construct builders directly. Any future
   command that forgets to replicate it gets silently wrong path resolution — this
   is structurally the same class of bug just fixed in `PlanBuildCommand`, just
   waiting to recur in the next new command.
2. **No consistency check between row 1 and row 2** for names that appear in both —
   today, disagreement is discovered only when a build fails with a misleading
   "source directory not found," not through any diagnostic command.
3. **No environment-bootstrap reconciliation.** CI environments that use their own
   native checkout mechanism (Azure Pipelines `resources.repositories`, GitHub
   Actions `actions/checkout`, etc.) have no equivalent of `strata repo add` wired
   into their pipeline today, and nothing in strata detects "the registered path
   doesn't exist / doesn't match reality" and offers to fix it.
4. **No enforcement that `solution.json` is actually gitignored** — the scaffold
   ships a correct `.gitignore` rule, but nothing catches a repo where the file was
   already tracked before that rule existed (a one-time `git rm --cached` migration
   step, or a `strata doctor`-style check, would close this).

None of this is a user misconfiguring anything: the tool currently offers two
knobs for one setting, wires only one of them into half its own commands, and has
no way to notice when a machine-local file has leaked into shared git history.

## Goal / Desired Outcome

1. Exactly **one** mechanism answers "where is repo/remote X on this machine, right
   now" — one resolution chain, consulted identically by every consumer.
2. No committed file contains a machine-specific path. Anything that varies
   between a developer's laptop and a CI runner is expressed out-of-band (env var,
   CLI flag, or a gitignored local overrides file), never in shared config.
3. A new command cannot get path resolution wrong by forgetting to replicate a
   pattern — there is one entry point, and not calling it is a compile/obvious
   error rather than a silent fallback to the wrong directory.
4. An unresolvable repo fails with an actionable error that names every way to fix
   it — not with a downstream "source directory not found" that looks like a
   misconfiguration of something else.
5. The default path (no env var, no flag, no link) requires **zero configuration**
   and works identically on a laptop and in CI.

## Considered Options

> **Note:** Options A–E below were written against the two-registry model and were
> the originally-recorded outcome (A + B + D). They are retained for the record;
> **Option F supersedes them** — see [Decision Outcome](#decision-outcome).

### Option A — Decouple `deploy_path` from checkout-path duty entirely

Tighten `RemoteModel.deploy_path`'s documented purpose and stop it being consulted
as a checkout-path fallback anywhere path resolution happens. Concretely:
- Update `RemoteModel.deploy_path`'s field description to explicitly say it is
  *never* a local-checkout path and must not be expected to agree with
  `solution.json`.
- Update `docs/config/manifest.md`'s existing (already-correct) distinction to add
  a one-line explicit warning against the anti-pattern seen in the example above.
- No functional/behavioral code change is required for this option by itself — it
  is already true today that `get_remote_map()` is never consulted for provisioner
  source-copying. The problem is purely that a real config author (correctly
  reading `RemoteModel`'s field description in isolation, without the ADR-0010
  context) inferred a checkout-path meaning that happens to look plausible. This is
  a documentation/model-description fix, low-risk, no migration needed.

### Option B — Collapse repo_map merge logic into one function

Add a single chokepoint — e.g. `SolutionController.get_effective_repo_map(configuration_service=None)`
— that does the `{**config_repo_map, **(repo_map or {})}` merge once, and have every
consumer (the four services *and* both build-command entry points) call it instead
of hand-rolling the merge or skipping it. This directly prevents the exact bug
class just fixed (a command that forgets to pass/compute `repo_map` at all).

**Trade-off:** touches `WorkspaceService`, `NamespaceService`, `CustomerService`,
`DeploymentService`, `RunBuildCommand`, `PlanBuildCommand` — six call sites — but
each change is a mechanical "replace the 2-line merge with one function call,"
low behavioral risk since the merge semantics don't change, only where the code
that performs them lives.

### Option C — Add a `strata repo doctor`/`--verify` consistency check

A new (or extended existing) command that, for every name appearing in *either*
registry:
- Confirms `solution.json`'s declared path exists on disk and looks like the
  expected repo (e.g. has a `.git` for `gitops` type).
- Warns (does not error — some environments legitimately only populate one
  registry) if the same name also appears in `configuration.spec.remotes` with a
  `deploy_path` that looks like a filesystem path but disagrees with `solution.json`.
- Could be wired into `strata guide`/workspace-readiness checks so it's surfaced
  proactively rather than only when a build fails.

**Trade-off:** genuinely new functionality (not a refactor of existing behavior),
larger scope, most valuable for catching the *next* instance of this drift rather
than fixing today's.

### Option D — Environment-variable path override

Allow a `STRATA_REPO_PATH_<NAME>` (or similar) environment variable to override a
registered repo's resolved path at runtime, without touching `solution.json` at
all. This would let CI set the correct path for its own checkout layout
declaratively in the pipeline YAML (`variables:`) without needing a `strata repo add`
step or a committed value that's wrong everywhere else.

**Trade-off:** a new resolution-precedence layer to document and test
(env var > `solution.json` > absent); doesn't by itself fix the git-tracking leak
or the duplicated merge logic, but directly solves the "CI genuinely needs a
different value than local dev, and there's no clean way to give it one today"
half of the problem (point 4 from the original report) without inventing a new
registry.

### Option E — Do nothing beyond the `PlanBuildCommand` fix already shipped

Leave `deploy_path` ambiguous, leave the merge duplicated, leave `solution.json`
tracked wherever it already is. Rejected: the `PlanBuildCommand` bug is a symptom,
not the disease — the same "forgot to thread repo_map" mistake can and will recur
in the next new command that constructs a builder or resolves an `@repo_name`
reference, and the git-tracking leak actively misleads whoever next reads that
example repo's `remotes.yaml` comment as authoritative.

### Option F — Identity-only declarations + tool-owned materialization + runtime override chain (chosen)

Options A–D all accept the two-registry model as a given and try to keep the two
registries honest (better docs, one merge function, an override layer, a
consistency checker). Option F asks a different question: *why does a committed
file contain a path at all?*

#### Prior art — what mature dependency-resolving CLIs actually do

This problem ("where is my other repository right now?") is well-trodden. The
prior art supports part of this proposal strongly and part of it only partially —
both are recorded honestly below, because an overstated claim is easy to knock
down.

**Strongly supported — out-of-band, non-committed overrides.** Every one of these
treats "I'm working on a local checkout of that dependency right now" as a
transient, per-machine fact expressed *outside* the shared manifest:

| Tool       | Override mechanism                      | Committed?                    |
| ---------- | --------------------------------------- | ----------------------------- |
| Nix flakes | `--override-input foo /path`            | No — CLI flag                 |
| Bazel      | `--override_repository=name=/path`      | No — CLI flag                 |
| Go         | `go.work`                               | No — gitignored by convention |
| Cargo      | `.cargo/config.toml` `paths`, `[patch]` | Typically machine-local       |
| npm/pnpm   | `npm link` / `pnpm link`                | No — machine state            |

Nix's `--override-input` and Bazel's `--override_repository` are almost exactly
the design proposed here and are the strongest support for it.

**Supported for *remote* dependencies — identity-only declaration, tool-owned
location.** For dependencies fetched from a registry or git remote, none of these
tools let the user configure *where* the content lands:

| Tool           | Committed declaration                      | Where it actually lands                               |
| -------------- | ------------------------------------------ | ----------------------------------------------------- |
| Go modules     | `require github.com/org/foo v1.2.3`        | `$GOMODCACHE` — tool-owned, keyed by identity+version |
| Cargo          | `foo = { git = "…", branch = "…" }`        | `~/.cargo/git/checkouts/…` — tool-owned               |
| Nix flakes     | `inputs.foo.url = "github:org/foo"`        | `/nix/store/…` — content-addressed                    |
| Bazel (bzlmod) | `bazel_dep(name = "foo", version = "1.0")` | output base — tool-owned                              |
| Terraform      | `source = "git::https://…"`                | `.terraform/modules/<key>` — tool-owned               |

**Not supported — "committed manifests never contain paths."** This claim would
be overstated. Several of these tools *do* allow committed local paths:

- Go's `replace github.com/org/foo => ../foo` lives in the committed `go.mod`.
- Cargo's `foo = { path = "../foo" }` is committed and idiomatic for workspaces.
- Terraform's `source = "../modules/vpc"` is committed and extremely common.

The honest reading is narrower, and is the one this ADR relies on: **committed
relative paths work only while every consumer shares the same directory layout.**
Go, Cargo and Terraform get away with it because their committed paths are
intra-project (a sibling crate inside the same repo, a module inside the same
Terraform tree). They are *not* being asked to describe where an independently
cloned repository landed on a machine the author has never seen — which is
precisely what strata is asking of `deploy_path`, and precisely where it breaks.

**Rule that does hold universally — when a path is committed, it is relative to a
root the tool owns.** Git submodules commit `path` in `.gitmodules`; Android's
`repo` tool commits `path` in its manifest XML. Both work — because the path is
always *nested under the workspace root*. Never `../sibling`, never absolute.

#### Where strata diverged

The current model commits `deploy_path: "../infra-repo"` — a path, pointing
*outside* the workspace root, in a team-shared file, describing where an
independently cloned repo landed. That is the one thing none of the prior art
does. And because there is no tool-owned materialization location, "where is it?"
becomes a genuine open question — which is why it then needs two registries to
answer, which then need to agree, which nothing enforces.

#### The proposal

**Two orthogonal axes, modelled separately.** A recurring flaw in the earlier
draft was collapsing these into one. They are independent and both must be
expressible:

| Axis         | Question                | Values                                                                                               |
| ------------ | ----------------------- | ---------------------------------------------------------------------------------------------------- |
| **Location** | *Where* is the content? | default (tool-owned cache) / overridden path / in-tree                                               |
| **Managed**  | *Who puts it there?*    | `managed: true` (strata clones + checks out the ref) / `managed: false` (something else already did) |

Today `gitops` vs `bundled` encodes the *managed* axis, and encodes it well —
that expressiveness must be preserved, not lost. All four combinations are legal
and each has a real use case: managed-at-default (plain local dev), unmanaged-at-
overridden (CI's authenticated native checkout), managed-at-overridden (put it
somewhere specific), unmanaged-at-default (an external tool populated the cache).

**Committed YAML declares identity + management intent — never a machine path:**

```yaml
remotes:
  # Ordinary remote — strata clones and pins it
  - name: infra
    url: https://github.com/org/infra.git
    ref: main
    managed: true

  # CI already cloned this with credentials strata doesn't have
  - name: env
    url: https://github.com/org/env.git
    ref: main
    managed: false

  # The workspace itself — see "self-reference" below
  - name: config
    in_tree: "."
```

**Location is resolved at runtime, first match wins:**

1. **Local overrides file** — a gitignored `.strata/overrides.yaml`, written by
   `strata repo link <name> <path>`. **CI uses this same file**, written by a
   bootstrap step, rather than a separate env-var mechanism (see below).
2. **`--repo <name>=<path>`** — CLI flag, for one-off invocations.
3. **`in_tree:`** — resolved relative to the workspace root (Rule-2-safe, committed).
4. **Tool-owned cache** — out-of-tree, keyed by identity + ref (see below).
5. **Hard error** naming the repo and every mechanism above. Never a silent
   fallback to `work_path` (this is Problem B — see that section).

##### Self-reference is a first-class case, not a corner

A workspace's own `config` directory is commonly declared as a remote pointing at
itself (`deploy_path: "."` today). **You cannot materialize the repository you are
currently executing inside**, so "tool owns the location" has no answer for it.
Today `type: bundled` accidentally covers this. The redesign needs an explicit
`in_tree: <relative-path>` form: never materialized, never overridable, resolved
against the workspace root. This is Rule-2 compliant and therefore genuinely safe
to commit.

##### Materialization belongs out of tree

The earlier draft proposed `.strata/repos/<name>`. That contradicts the prior art
it cites — Go, Cargo, Bazel and Nix all materialize *outside* the project tree,
keyed by identity+ref and shared across workspaces. In-tree materialization has
three concrete problems: nested `.git` directories inside a parent repository
(which git handles poorly — neither submodules nor ignored by default), multi-
hundred-megabyte checkouts inside a directory that is otherwise small CLI state,
and no reuse between two workspaces that depend on the same infra repo.

**Decision: materialize out of tree**, in a user-level cache keyed by identity+ref
(`$STRATA_CACHE_HOME` or an OS-appropriate default), matching the prior art. A
workspace-local location remains reachable via `repo link` for anyone who wants it.

##### One override mechanism, not three

The earlier draft had env var *and* CLI flag *and* a link file — with an env-var
naming scheme (`iac-int` → `STRATA_REPO_IAC_INT`?) that needs a normalisation rule
and invites collisions, case bugs, and hyphen/underscore ambiguity.

**Instead: CI and local dev use the same gitignored overrides file.** CI writes it
in a bootstrap step; a developer writes it via `strata repo link`. This collapses
three mechanisms to two (file + one-off CLI flag), removes the naming footgun
entirely, and makes the CI and developer paths *identical* rather than merely
analogous — which also means a developer can reproduce a CI resolution locally by
copying one file.

**The real-world scenarios then resolve as:**

| Scenario                                                                                        | What the user does                                                   |
| ----------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| Local dev, let strata fetch everything                                                          | Nothing. `strata repo sync` populates the cache. Zero configuration. |
| Local dev, actively editing a sibling checkout                                                  | `strata repo link infra ../infra`                                    |
| CI, no native checkout step                                                                     | Nothing. `strata repo sync` populates the cache.                     |
| CI *with* an authenticated native checkout (Azure `resources.repositories`, `actions/checkout`) | Bootstrap step writes the overrides file; overrides `managed: false` |
| The workspace's own config directory                                                            | `in_tree: "."` — never materialized                                  |

##### A lock file is a prerequisite, not an optional extra

The cache design cannot be settled without first answering a question the earlier
draft never asked: **is there a lock file?** Without one there is no good cache
key; with one the concurrency problem largely dissolves.

The issue is that `ref: main` is **mutable**. If the cache key contains a branch
name, the directory's contents are a moving target. "Two workspaces want different
refs" is the *easy* case — the hard case is two workspaces wanting the *same* ref
and disagreeing about when it moved: workspace A syncs Monday, workspace B syncs
Friday, `main` advanced in between, and they now silently need different content
at the same key. Any key derived from a mutable ref has this problem regardless of
how concurrency is handled.

Every tool cited as prior art solved this the same way — key on the **resolved
immutable identity**, and record the resolution in a lock file:

- **Cache key:** `<cache_root>/<hash(normalized_url)>/<commit_sha>`. Immutable, so
  a populated directory is never mutated and never invalidated — safe to share
  across workspaces and across concurrent processes with zero read coordination.
- **Lock file:** committed, mapping `name → (url, requested_ref, resolved_sha)`,
  updated only by an explicit `strata repo sync --update` (the `go get -u` /
  `cargo update` / `nix flake update` verb). This is what makes builds
  reproducible and what makes `--dry-run` mean anything.

With this, "two workspaces want different refs" is not a conflict at all —
different SHAs, different directories, both present, both immutable.

**Cost to be honest about:** a lock file changes `ref: main` from "always follow
the branch" to "pin until told otherwise." Some users want the former. That is a
real behavioural change and needs its own migration note — though it is moot for
`managed: false` remotes, which covers the CI case entirely.

##### Cache implementation specifics

These are recorded now because each is a known way to get this wrong:

- **Cache root:** `%LOCALAPPDATA%\strata\cache\repos` on Windows;
  `$XDG_CACHE_HOME/strata/repos` (default `~/.cache/strata/repos`) elsewhere;
  overridable via `STRATA_CACHE_DIR`. The override matters for CI — hosted agents
  want the cache inside the workspace so it lands in the pipeline cache, and
  air-gapped builds want it pre-seeded.
- **Concurrent materialization:** clone/fetch into `<cache_root>/tmp/<random>`,
  then atomically rename into place. The loser of a race treats "destination
  exists" as success and discards its temp copy. No lock files, no timeouts, no
  stale-lock recovery after a crash — which matters on Windows, where advisory
  locking and crash cleanup are both worse than on POSIX. **The temp directory
  must live under the cache root, not the system temp directory**, or the rename
  crosses filesystems and stops being atomic. This is the single most common way
  this pattern is implemented incorrectly.
- **Windows rename semantics:** `os.rename` fails if the destination exists
  (unlike POSIX), so the exists-is-success path must be taken deliberately rather
  than relying on replace semantics. Expect transient `Access is denied` when
  another process holds a handle in the directory, and retry once.
- **Fetching by SHA:** `git fetch --depth 1 origin <sha>` requires
  `uploadpack.allowReachableSHA1InWant` on the server, which is **not** enabled by
  default on GitHub Enterprise. A fallback is required (fetch the ref, then check
  out the SHA, verifying reachability) or this will work against github.com and
  fail against on-premise GHE.
- **Garbage collection:** an immutable content-addressed cache grows without
  bound. `strata cache gc` with last-access pruning is needed eventually. Recorded
  here as a follow-on rather than a blocker, so that it is a planned item and not
  a disk-full incident.

##### The overrides file covers `managed:` as well as location

`managed:` must be overridable, **in both directions**:

- Committed config says `managed: true`; a CI pipeline with an authenticated
  native checkout needs "don't fetch, it's already here" → `true → false`.
- Committed config says `managed: false` (because CI provides it); a developer
  with no checkout wants strata to fetch it → `false → true`.

If the overrides layer cannot express these, the only remaining place to express
them is the committed file — which is precisely the problem Option F exists to
eliminate. **`managed:` is not a property of the dependency; it is a property of
whether *this environment* already provides it.** That makes the committed value a
*default*, and defaults are exactly what an overrides layer overrides.

Three qualifications:

1. **`in_tree` is not overridable.** It is genuinely invariant — you can never
   materialize the repository you are executing inside. Different axis, fixed.
2. **Validate the 2×2 rather than assuming it.** `managed: false` + no location is
   a hard error (nobody knows where it is). `managed: true` + explicit location is
   legitimate but escapes the content-addressed cache and its GC — allow it, and
   document that it opts out of sharing. The other two are the normal cases.
3. **An unknown repo name in the overrides file is a hard error**, listing the
   known names. A typo'd `iac-itn` silently creating a phantom override, or
   silently doing nothing, is the *same silent-fallback bug class* Track 1 closes
   — it must not be reintroduced one layer up.

##### Provenance must be a first-class output

Once there is a committed layer, an overrides layer, and `managed:` varying
independently, the failure mode being escaped stops being "multiple sources" and
becomes **"multiple sources with no visibility into which one won."** That is what
made the original incident expensive — not that the two registries disagreed, but
that nothing reported *that* they disagreed or *which* was in effect.

`strata repo status` must therefore show, per repo and per axis, which layer
supplied the effective value — e.g. location from `overrides.yaml`, `managed`
from the committed declaration, ref from the lock file. Nix (`nix flake metadata`)
and Bazel (`bazel info`) both do this, and it is what makes a layered resolution
chain debuggable rather than a repeat of the current situation.

This is cheap: the resolver must already know which layer won in order to select a
value. It only has to stop discarding that information.

#### Why this dissolves the problems rather than patching them

The three competing answers from the table at the top of this ADR collapse into
**one committed declaration plus one runtime resolution chain**. Specifically:

- **Issue 1 (duplicated merge logic)** — there is only one resolution path to
  implement, so there is no merge to duplicate and nothing for a new command to
  forget. The `PlanBuildCommand` bug class becomes unrepresentable rather than
  merely fixed.
- **Issue 2 (no consistency check between registries)** — there are no longer two
  registries holding the same fact, so there is nothing to check for agreement.
- **Issue 3 (no CI reconciliation)** — CI either uses the tool-owned cache (no
  config) or writes the overrides file in a bootstrap step. No committed value
  ever has to be wrong somewhere.
- **Issue 4 (`solution.json` git-tracking leak)** — **this is the important one:**
  once `solution.json` holds no machine-specific paths, there is nothing
  machine-specific left in it to leak. It no longer *needs* to be gitignored. The
  tracking problem stops existing rather than requiring a `git rm --cached` plus
  a policing check to keep it from recurring.

**Trade-off:** this is the largest-scope option — a breaking schema change
(`deploy_path` stops being a checkout path), a new materialization cache
convention, a new override chain, and new `strata repo link` behaviour. It needs
a migration path for existing workspaces. In exchange it removes an entire class
of problem instead of adding three mechanisms to manage it.

**Option F does *not* by itself fix Problem B.** See the decision below.

## Decision Outcome

**Two independent tracks, shipped separately.** The originally-recorded outcome
(A + B + D) is superseded, but so is the earlier framing that Option F alone is
sufficient.

### Track 1 — Fix Problem B now (non-breaking, patch release, no ADR needed)

This is the change that removes the actual damage from the reported bug, and it
is orthogonal to the schema question:

1. **Delete the `work_path` silent fallback** in every builder's
   `_copy_provisioner_source()`. An unresolvable repo name becomes a hard error
   naming the repo and how to fix it — never a quiet resolution to the wrong
   directory.
2. **Stop passing location resolution as an optional kwarg.** Make it a required
   constructor dependency (injected resolver) or a service lookup, so that
   "forgot to pass `repo_map`" becomes structurally impossible rather than
   silently degrading.
3. **Fix `SolutionController.get_repo_map()`'s `os.getcwd()` branch** to use
   `self._work_path`, which the method already holds and already uses for `gitops`
   repos. This closes the live, reproducible "running strata from a subdirectory
   silently resolves `@ref`s to the wrong place, ignoring an explicitly-supplied
   `--work-path`" failure documented above. One line, non-breaking, and
   independent of every schema decision in Track 2.

Without step 2, Option F's central claim — *"one resolution path, so there is
nothing for a new command to forget"* — **does not hold.** If a future unified
resolver is still handed to builders as an optional dict, the next new
`strata build <thing>` omits it exactly as `PlanBuildCommand` did, and gets the
same silent fallback. Shipping the breaking schema change while leaving the
optional-kwarg pattern in place would keep the bug class alive under a new schema.

Track 1 also stands on its own merits: it is a strict improvement even if Option F
is never adopted.

### Track 2 — Adopt Option F for Problem A (breaking, deprecation window)

Adopt Option F as specified above, including the corrections folded in from
review: orthogonal `location`/`managed` axes (both overridable), first-class
`in_tree` self-reference, out-of-tree materialization keyed by resolved commit SHA
backed by a committed lock file, a single overrides-file mechanism shared by CI
and local dev, and provenance as a first-class output of `strata repo status`.

**Migration is a deprecation window, not a hard break** — and not for politeness.
`deploy_path` is not being *removed*; its *meaning* changes. A silent semantic
change is the worst possible failure mode here, because a stale value keeps
parsing and validating cleanly and only misbehaves at deploy time in CI. Ship:

1. Honour the legacy `deploy_path`-as-location with a loud deprecation warning.
2. A `strata validate` / `doctor` check that flags workspaces still relying on the
   old meaning — so it surfaces at validate time, not at `terraform apply` time.
3. Remove the legacy meaning in the following major version.

### Disposition of the earlier options

- **A** (tighten `deploy_path` docs) — superseded by Track 2: `deploy_path` stops
  being a location field, so there is no ambiguous meaning left to document
  against. Worth doing opportunistically during the deprecation window.
- **B** (single merge function) — superseded by Track 2 for the merge itself, but
  its *spirit* is preserved and strengthened in Track 1 step 2 (one injected
  resolver rather than one shared helper that callers may still forget to call).
- **C** (`strata repo doctor`) — retained, reduced in scope, and promoted: it is
  now part of Track 2's migration tooling (item 2 above), not an optional extra.
- **D** (env-var override) — **rejected as specified.** The intent is absorbed
  into Track 2, but via the shared overrides file rather than one env var per
  repo, which avoids the name-normalisation footgun.
- **E** (do nothing) — remains rejected.

The `solution.json` git-tracking remediation is retained in Remaining Work as a
transitional step only — after Track 2 the file holds nothing machine-specific.

## Remaining Work

### Track 1 — Problem B (non-breaking, ship first)

| Item | Description                                                                                                                                                                                                                                                  | Status |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------ |
| B-1  | Remove the `work_path` silent fallback in every builder's `_copy_provisioner_source()`; unresolvable repo name → hard error naming the repo and the fix                                                                                                      | 🔲 TODO |
| B-2  | Replace the optional `repo_map` kwarg with a required injected resolver (or service lookup) so omission is structurally impossible                                                                                                                           | 🔲 TODO |
| B-3  | Regression test: a builder constructed without a resolver fails loudly rather than resolving against `work_path`                                                                                                                                             | 🔲 TODO |
| B-4  | Reconcile the duplicated resolution logic proven divergent above (`repo status` vs `get_repo_map()`, and `get_repo_map()`'s own `local`/`gitops` split over `url`/`path` and `os.getcwd()`/`work_path`)                                                      | 🔲 TODO |
| B-5  | Fix `get_repo_map()`'s `os.getcwd()` → workspace root for `type: local` (and the identical bug in `generate_workspace()`); regression test asserting identical resolution from the workspace root and from a nested subdirectory with the same `--work-path` | ✅ DONE |
| B-6  | Replace the misleading "check your profile refs" diagnostic with one that reports the resolved path and the repo whose resolution produced it                                                                                                                | 🔲 TODO |

### Track 2 — Problem A (breaking, deprecation window)

| Item                         | Description                                                                                                                                                               | Status                 |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------- |
| F-1                          | Define the remote schema: identity (`name`/`url`/`ref`) + `managed:` axis + `in_tree:` self-reference form                                                                | 🔲 TODO                 |
| F-2                          | Implement the resolution chain: overrides file → `--repo` flag → `in_tree` → cache → hard error; unknown name in overrides = hard error listing known names               | 🔲 TODO                 |
| F-3                          | Lock file: committed `name → (url, requested_ref, resolved_sha)`, updated only by explicit `strata repo sync --update`                                                    | 🔲 TODO                 |
| F-4                          | Content-addressed cache keyed by `hash(normalized_url)/<commit_sha>`; temp-dir-under-cache-root + atomic rename; exists-is-success on race; Windows rename/retry handling | 🔲 TODO                 |
| F-5                          | SHA-fetch fallback for servers without `uploadpack.allowReachableSHA1InWant` (fetch ref, then check out SHA, verify reachability)                                         | 🔲 TODO                 |
| F-6                          | `strata repo link <name> <path>` writes the gitignored overrides file; document the same file as the CI bootstrap mechanism                                               | 🔲 TODO                 |
| F-7                          | `managed:` overridable in both directions; validate the location×managed 2×2 (`managed: false` + no location = hard error)                                                | 🔲 TODO                 |
| F-8                          | Provenance in `strata repo status`: per repo, per axis, which layer supplied the effective value                                                                          | 🔲 TODO                 |
| F-9                          | Honour legacy `deploy_path`-as-location with a loud deprecation warning                                                                                                   | 🔲 TODO                 |
| F-10                         | `strata validate`/`doctor` check flagging workspaces still relying on the legacy meaning (surfaces at validate time, not apply time)                                      | 🔲 TODO                 |
| F-11                         | Migration note: lock file changes `ref: main` from "always follow branch" to "pin until told otherwise" (moot for `managed: false`)                                       | 🔲 TODO                 |
| F-12                         | Document the resolution chain + `managed:` override CI pattern in the `.azure`/`.github` scaffold templates                                                               | 🔲 TODO                 |
| F-13                         | `strata cache gc` with last-access pruning — an immutable cache grows without bound                                                                                       | 🔲 DEFERRED             |
| F-14                         | Remove the legacy `deploy_path` location meaning (following major)                                                                                                        | 🔲 DEFERRED             |
| Ops (transitional, external) | `git rm --cached .strata/solution.json` in affected deployment repositories — needed only until Track 2 lands                                                             | 🔲 TODO (external repo) |

## Open Questions

> Two previously-open questions — cache root/key derivation, and whether the
> overrides file may cover `managed:` — are now **resolved** and folded into
> Option F above (lock file + content-addressed cache; `managed:` overridable in
> both directions, with three qualifications).

1. **Does `in_tree` need to support anything other than the workspace root?**
   `"."` covers the known case. Allowing arbitrary nested paths is Rule-2-safe but
   adds surface area with no demonstrated demand.
2. **Should Track 1's hard error be a new exit code**, or reuse the existing
   validation/system-error codes? A dedicated code would let CI distinguish
   "unresolvable repo" from a genuine build failure.
3. **Lock file placement and commit status.** Committed is the intent — that is
   what makes builds reproducible — but it needs a home. Alongside the
   `config/*.yaml` sources, or in `.strata/`? The latter currently signals
   "generated, gitignored," which would be exactly the wrong signal for a lock
   file.
4. **Does `strata repo sync --update` operate per-repo or all-or-nothing?**
   `go get -u <pkg>` and `cargo update -p <pkg>` support both; the default matters.

