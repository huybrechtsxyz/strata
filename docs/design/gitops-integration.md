# GitOps Integration (ArgoCD / Flux) — v2 Design

- Status: All 5 phases implemented and code-reviewed. Schema field;
  `push_file()`/`remove_file()`/`ensure_checkout()`; the
  `ArgoCDIntegration`/`FluxIntegration` classes, registered and wired end
  to end; `prepare_git_credentials()` (`ssh_key`/`token` methods,
  including the `passphrase`/`username` sub-fields a full review found
  were modelled but initially unwired); and `deploy_controller.py` now
  resolves a GitOps remote's `integration` to a real `AuthenticationModel`
  fresh at deploy time, threading it (with `resolved.values`) down to
  `plan()`/`deploy()`/`collect_step_outputs()` — `tool: argocd`/`flux`
  works end to end today, ambient or with real `ssh_key`/`token`
  credentials. Two code review passes (2026-10-02) found and fixed: a
  path-traversal gap (`ProvisionerGitOpsModel.output_file` and
  `SolutionRemoteModel.reference` now both validated); a self-introduced
  tempdir leak in the passphrase fix itself; and `IntegrationError`
  (credential misconfiguration) being able to escape `BaseGitOpsIntegration`'s
  methods and `_git_clone()` completely uncaught, crashing with a raw
  traceback instead of a clean diagnostic — both now converted at the
  source, consistent with this class's/module's own "never raises"
  contracts. Remaining, explicitly out of scope: no `deploy destroy`/
  `strata destroy run` command exists yet for *any* tool (pre-existing
  gap, not created here), a full `SourceIntegration` capability class
  (ADR-0021 D9) remains unbuilt, and there is no locking around a shared
  GitOps push checkout for concurrent `deploy run`s (inherited from
  `audit_push.py`'s own pre-existing pattern).
- Last updated: 2026-10-02

## Overview

v2's schema already fully anticipates ArgoCD/Flux as `ProvisionerType`
members (`SYNC_PROVISIONER_TYPES = {ARGOCD, FLUX}`,
`strata/utils/builtin_types.py`) — a sync/GitOps-style provisioner needs no
`source`, since it "renders from the platform artifact instead". Both
`build_controller.py`'s `build_run()` and `deploy_controller.py`'s
`deploy_run()` already special-case a source-less provisioner's path
computation correctly (confirmed consistent on both sides, including a
real fix already landed in deploy: "source-less/sync-GitOps provisioners
still key off `step.name`, matching build's own other branch").

**What's missing is the actual `Integration` class** — no `ArgoCDIntegration`/
`FluxIntegration` is registered anywhere (`strata.integrations.registry._KNOWN`
has no `"argocd"`/`"flux"` entry, and they're not even in `_KNOWN_V1_TYPES`'s
friendlier "not yet ported" hint list). Declaring `tool: argocd` today and
running `build run`/`deploy run` hits a bare `IntegrationNotFoundError`.

**The real mechanism is much simpler than "talk to Kubernetes/ArgoCD/Flux"**
— confirmed by reading v1's actual implementation directly
(`e:\SourcesXYZ\strata\src\strata\deployers\sync_deployer.py`,
`BaseSyncDeployer`, shared by both ArgoCD and Flux): **it's a git
commit-and-push, nothing more.** The in-cluster GitOps controller (already
running, entirely outside strata's scope) watches a git repo on its own
poll/webhook cycle; strata's only job is to render a config file and push
it there. No Kubernetes API, no ArgoCD/Flux API call anywhere in the actual
deploy path — those controllers' own API is only ever queried for a
read-only status check (`health`), never to trigger anything.

## Current Design (v1) — evidence, read directly from `BaseSyncDeployer`

Shared step sequence for both ArgoCD and Flux (only `get_deployer_name()`
differs between the two subclasses):

| Step      | What it actually does                                                                             |
| --------- | ------------------------------------------------------------------------------------------------- |
| `setup`   | No-op — nothing to initialise                                                                     |
| `check`   | `git -C <repo> fetch --dry-run` — confirms the local clone of the config repo is reachable        |
| `plan`    | `git -C <repo> diff --no-index -- <existing> <rendered>` — shows what would change, or "new file" |
| `apply`   | Copy the rendered file into the config repo -> `git add` -> `git commit` -> `git push`            |
| `destroy` | Remove the file from the config repo -> `git add` -> `git commit` -> `git push`                   |
| `health`  | Queries the GitOps controller's own API for reconciliation status — **read-only**, optional       |
| `output`  | Returns the commit SHA + the file's path in the remote repo                                       |

Configuration v1 needs per stage: `stage.backend.integration` (names an
`Integration` document whose `properties.output_file` says which file to
render) + `stage.backend.remote` (names the already-locally-cloned config
repo to push into). `SyncBuilder` (the build-time half, not read in detail
here) is what actually produces the rendered file `sync_deployer.py` later
commits — out of scope for this doc to re-derive; the key fact is the
render itself is **not** GitOps-specific machinery.

## Design (v2 proposal)

### Schema — one new typed field, same shape as `backend`/`properties`

`ProvisionerModel` already has the precedent for "a field only meaningful
for one tool" (`backend` for terraform, `properties` for ansible, each
validated against `tool` by a `model_validator`). A GitOps provisioner
needs the same treatment — not v1's loose `stage.backend.integration`/
`.remote` (reusing a field already earmarked for terraform state config in
v2's schema would be a type lie, the exact anti-pattern `RemoteFetch`'s own
docstring already flags elsewhere in this schema):

```python
class ProvisionerGitOpsModel(PlatformBaseModel):
    """Only valid when `tool` is a SYNC_PROVISIONER_TYPES member (argocd, flux)."""

    remote: Annotated[PlatformName, RemoteReference()] = Field(
        description="The already-declared git remote (strata.yaml spec.remotes) holding the GitOps config repo."
    )
    output_file: str = Field(
        description="Path, relative to the remote's root, of the file this provisioner renders and pushes."
    )
```

Added to `ProvisionerModel` as `gitops: ProvisionerGitOpsModel | None`,
gated by a `model_validator` mirroring `validate_source_required_unless_sync()`'s
own "only recognized tools are checked" leniency (an unrecognized custom
`tool` is never rejected either way).

**Worked example** — an ArgoCD provisioner pushing a rendered Application
manifest into an already-declared `gitops-config` remote, with no `source`
(sync-type, same as a `terraform`/`helm` provisioner would need one):

```yaml
# strata.yaml
spec:
  remotes:
    - name: gitops-config
      type: git
      url: https://github.com/acme/gitops-config.git
      reference: main
      fetch: strata
      integration: github-deploy-key   # declared, but NOT YET WIRED to anything — see "Authentication" below

# workspace.yaml
spec:
  provisioners:
    - name: forge_sync
      tool: argocd
      gitops:
        remote: gitops-config
        output_file: apps/forge/values.yaml
      output:
        template: templates/forge-values.yaml.j2   # renders the actual file content
```

No `source:` block — `validate_source_required_unless_sync()` already
accepts this for a recognized sync tool. `output.template` supplies the
file content (already-built, tool-agnostic); `gitops.remote`/`.output_file`
supply only the *destination* — the one part that's new.

### Rendering — already built, no new machinery needed; and we only ever render a values/config file, never the Application/HelmRelease CR itself

`output.template` (`OutputModel`, "valid for any tool") + `InfraIntegration.
render_output_template()` already exist and are tool-agnostic by design
(ADR-0023 D3's own reasoning: "nothing about this field is tool-specific").

**Resolved (2026-10-01), per direct question** ("do we need to create the
argocd application, or just the updated helm chart? would a build run
with the graph be enough to generate a sample with jinja2?"): confirmed
against v1's real `builders/sync_builder.py` (not read in detail before
this — now closed) — v1's `SyncBuilder` renders **one plain Jinja2
template** from the full platform context (`PlatformSpecModel`, secrets
excluded) to `properties.output_file`, with zero ArgoCD/Flux-specific
knowledge of what that file *means*. It is genuinely file-shape-agnostic
at the mechanism level.

But the *right* answer for what that file should actually contain is:
**just the updated values/config file — never the ArgoCD `Application` /
Flux `HelmRelease`/`Kustomization` CR itself.** That CR is a one-time
bootstrap resource (created once, out of band, pointing at a fixed path
in the config repo — e.g. `valueFiles: [apps/forge/values.yaml]`); the
GitOps controller re-reads whatever that path resolves to on every poll.
Re-rendering and re-pushing the CR itself on every deploy would mean
strata re-asserting ownership of a resource ArgoCD/Flux itself usually
owns the lifecycle of (and risks fighting ArgoCD's own self-heal if the
CR's `status`/annotations strata doesn't know about get clobbered on
every push) — the CR is exactly the "already exists, don't regenerate it"
category, the same reasoning that already keeps `platform.json`/
`PlatformBuilder`'s snapshot out of v2's scope. v1's own real config
confirms this shape too: `properties.output_file` is always a single
values/config file path (`apps/forge/values.yaml`-shaped), never an
Application-manifest path.

So: **yes, `build_run()`'s already-resolved `graph` + `output.template`
is already sufficient** — no new rendering capability needed, and no
ArgoCD/Flux-specific template logic belongs in strata at all. The
bootstrap Application/HelmRelease CR (if/when needed) is a separate,
out-of-band concern — likely a `strata sln init`-style one-time scaffold
if it's ever built, not part of `build run`/`deploy run`'s per-deploy loop.

A GitOps provisioner's values/config file is rendered exactly the same
way Terraform's `variables.json.j2`/`output.template` already is —
`default_output()` only needs a GitOps-specific override if there's ever
a sensible *default* projection with no template; until a real shape is
evidenced, requiring `output.template` to be set (raising otherwise) is
the honest default.

### Authentication — fetch (read) and push (write), looked at together

Reviewed directly, per request ("let's look at how we would authenticate
for both types of repos"): both `strata.yaml spec.remotes` (fetch — used
by every provisioner/module `source:`) and this design's new GitOps
`remote:` (push) are the exact same underlying primitive — a
`SolutionRemoteModel` naming a git URL + an optional `integration` for
credentials — so they should be designed as one capability with two
directions, not two separate auth stories.

**Today, neither direction actually authenticates through strata at all.**
Already established for fetch (`_git_clone()`'s own docstring: "No
credential handling yet... relies entirely on whatever git already has
configured"); the same is true for push by construction, since
`push_file()` (this design) is built on the identical `run_command()` git
CLI pattern with no credential setup step of its own either. Both
directions are currently "ambient only" — whatever SSH agent/credential
helper/`~/.gitconfig` the process already has.

**This has never been forced before now, for a concrete reason:** every
real fetch remote checked so far (haven, config-deploy) is either public
or already reachable via the operator's/CI runner's own pre-configured
git auth — so "ambient only" has quietly been sufficient. **Push is
different in kind, not just direction** — a write to a remote repo
essentially never works anonymously, so a real GitOps push is the first
concrete case that *forces* the question "how does strata authenticate a
git operation" to actually be answered, rather than continuing to lean on
whatever the host happens to have configured.

**The capability shape, if/when built — one `SourceIntegration`, both
directions:** `Capability.SOURCES` already exists in `integration_model.py`
(declarable, no ABC yet — ADR-0021 D9 names it as a real, scheduled
future capability, not speculative). The natural design is **one**
`SourceIntegration` class serving both `fetch`/`checkout` (today's
`resolve_remote()`/`_git_clone()`, which would move under it) and this
design's `push` — same transport (`run_command()` wrapping the real `git`
CLI), same credential setup step, differing only in the git subcommand
run at the end (`clone`/`checkout` vs `add`/`commit`/`push`). Splitting
fetch and push into two different credential mechanisms would be modeling
an implementation accident (which direction happens to need write access)
as if it were a capability difference, when it isn't one.

**"Can we just assume the pipeline/CLI's own identity already has
access?" — only for the same-repo case; not for the realistic GitOps
one.** Checked directly, per that exact question:

- **Same-repo push** (the GitOps config lives in the same repo the
  pipeline already checked out): plausible the ambient identity already
  has write access. v2's own real CI confirms this pattern works —
  `ci-release.yml`/`ci-docs.yml` push (a release, `gh-pages`) using
  nothing but GitHub Actions' auto-issued `secrets.GITHUB_TOKEN`, scoped
  to that one repo, no extra credential configured anywhere.
- **Cross-repo push** (the GitOps config lives in a *separate*, shared
  repo from the app's own source — the far more common real GitOps
  topology, since the whole point of a GitOps config repo is usually to
  be shared across many apps/teams, not per-app): **the ambient identity
  essentially never has access.** This is not a strata gap at all — it's
  a hard platform boundary: GitHub Actions' auto-`GITHUB_TOKEN` is
  cryptographically scoped to only the triggering repository by GitHub
  itself, regardless of what permissions the workflow YAML requests; it
  cannot be granted access to a different repo. An Azure DevOps pipeline's
  SPN *can* be granted cross-repo access (ADO's permission model is
  per-org/per-project, not per-run like GitHub's) — but only if someone
  deliberately provisioned that grant, never automatically. **This
  matches real prior-project experience exactly** (explicit note: earlier
  projects needed to fetch a `GITHUB_TOKEN`/PAT specifically because the
  ambient pipeline identity did not have cross-repo push rights) — the
  cross-repo case is the one that actually forces explicit credential
  wiring, not a hypothetical.
- **The fallback-chain pattern to reuse already exists in this codebase**
  — `AzureKeyVaultResolver` (`strata/integrations/azure_keyvault_resolver.py`)
  wraps `azure.identity.DefaultAzureCredential`, which tries managed
  identity / workload identity / environment / `az login`, in that order,
  with **zero strata-specific configuration** needed for the common case,
  falling back to explicit credentials only when ambient auth doesn't
  apply. `SourceIntegration` should follow the identical shape: default to
  ambient git config (same-repo case, zero config, matches today's actual
  `_git_clone()` behavior exactly), and let `remote.integration` +
  `AuthenticationModel` supply an **explicit** token/PAT/deploy key only
  when the remote being pushed to isn't the one the ambient identity
  already covers — which is the realistic GitOps case, not the exception.

**The real open gap, found while checking this: `AuthenticationModel` has
no git-native method at all — in either v1 or v2.** Read both directly:
v2's closed `method` vocabulary (`oauth2`/`aws`/`gcp`/`api_key`/
`certificate`/`saml`/`cli`/`managed_identity`) is an exact match for v1's
own — not a v2 gap, an inherited one. None of these cleanly models "an
SSH deploy key" or "an HTTPS PAT embedded in the clone/push URL," the two
real-world git credential shapes:

- **`method: cli`** is the closest honest fit for *today's actual
  behavior* — its own description, "uses existing CLI authentication...
  no credential references needed, uses ambient credentials," is a verbatim
  match for what `_git_clone()` already does. Declaring `integration:
  <name>` with `method: cli` on a remote would mean "yes, ambient git
  config is deliberate here," not silence about it.
- **A real deploy key or PAT needs a new shape.** `certificate` (
  `certificate_path`/`private_key_path`) could be repurposed for an SSH
  key (the private-key field genuinely fits), but its naming/other fields
  (`ca_bundle_path`, `certificate_password`) are TLS-flavored, not SSH's
  shape. `api_key` could carry a PAT string, but its own `header_name`
  field assumes HTTP-header delivery, which is not how git consumes a PAT
  (embedded in the URL's userinfo, or via a credential helper) — using it
  would need a git-specific consumption path ignoring half the model's
  fields.

**First design pass (2026-10-01), per direct request** — two new methods,
added to `AuthenticationModel`'s closed `method` vocabulary rather than a
separate git-only auth model: `SolutionRemoteModel`'s own docstring
already states the philosophy this should match — "Credentials are never
inline here — Integration is strata's single credential mechanism" — one
shared vocabulary, not a parallel one just for git. Follows the exact
existing pattern every other method already uses (one small model, every
field a key reference, never an inline secret):

```python
class SSHKeyAuthenticationModel(PlatformBaseModel):
    """SSH private key authentication (git deploy keys). All fields are
    key references resolved from environment declarations."""

    private_key: str = Field(description="Key reference for the SSH private key (PEM/OpenSSH format)")
    passphrase: str | None = Field(None, description="Key reference for the private key's passphrase, if encrypted")
    known_hosts: str | None = Field(
        None, description="Key reference for a known_hosts entry pinning the remote host's key"
    )


class TokenAuthenticationModel(PlatformBaseModel):
    """Bearer/PAT token authentication (git HTTPS remotes). All fields are
    key references resolved from environment declarations, except
    `username` (not secret — paired with the token for Basic-auth-shaped
    delivery, e.g. 'x-access-token' for GitHub, any non-empty string for
    Azure DevOps)."""

    token: str = Field(description="Key reference for the token/PAT value")
    username: str | None = Field(None, description="Literal username to pair with the token — not a key reference")
```

Added as `method: Literal[..., "ssh_key", "token"]` + `ssh_key:
SSHKeyAuthenticationModel | None` / `token: TokenAuthenticationModel |
None` fields, `validate_method_matches_populated_config()`'s
`method_fields` tuple extended to match — no change to that validator's
logic, only its field list. Not reusing `certificate`/`api_key` for the
reasons above: forcing a new concept through an existing model's
unrelated fields (TLS cert paths, an HTTP header name) is the same
"validates fine, silently wrong" risk `AuthenticationModel`'s own
`validate_method_matches_populated_config()` docstring already names as
the exact bug class this model is designed to prevent.

### Credential delivery mechanics — keeping the value out of `argv`

Resolving *which* secret to use (above) is already solved — the open
problem is getting that value to the actual `git` subprocess without
leaking it. Embedding a token directly in the clone/push URL
(`https://x-access-token:<token>@host/...`) is the simplest approach and
is explicitly **not** safe enough to be the only option: it puts the
token in `argv`, visible via `ps`/Task Manager to any user on the same
host — the exact leak class `run_command()`'s own docstring already
names as the reason secrets go through `input`/`env`, never `args`
("visible in `ps`/Task Manager, unlike stdin"). Two real, safe mechanisms
instead, both expressible purely through `run_command()`'s existing
`env=` kwarg — no new transport:

- **`method: ssh_key`** — write the resolved private key to a short-lived
  temp file (`0600` permissions), set `GIT_SSH_COMMAND="ssh -i <path>
  -o UserKnownHostsFile=<known_hosts path, if supplied>"` in `env=`, and
  delete the temp file in a `finally` once the git subprocess returns
  (success or failure). No new transport — same `run_command()` every
  other integration already uses.
- **`method: token`** — set `GIT_ASKPASS` (in `env=`) to a tiny bundled
  helper script that echoes a token read from *another* env var (also
  set in `env=`, never in `args`) — git itself invokes `GIT_ASKPASS` to
  obtain the password, so the token is never embedded in the URL or any
  command-line argument at any point.

Both would live in one small new function (sketch, not yet built):

```python
def prepare_git_credentials(auth: AuthenticationModel | None) -> tuple[dict[str, str], Callable[[], None]]:
    """Returns (env overrides for run_command, a cleanup callback).

    method: cli / None -> ({}, no-op) — today's actual ambient behaviour, unchanged.
    method: ssh_key     -> writes a temp keyfile, returns GIT_SSH_COMMAND + a cleanup that deletes it.
    method: token       -> returns GIT_ASKPASS + the token's own env var, no filesystem cleanup needed.
    """
```

Used identically by both directions — `_git_clone()` (moved under
`SourceIntegration` once it exists) and this design's `push_file()` —
confirming again that fetch and push are one capability, not two.

**Still not fully resolved, deliberately**: this is a real, buildable
first design, not a decision to implement unscheduled — it still needs a
real consumer to validate the two method shapes against (does a real
deploy key ever need a passphrase? does a real PAT ever need anything
beyond `token`/`username`?) before committing to it in code, same "don't
guess a shape with no real contract" discipline as everywhere else in
this doc.

### The one real missing primitive — git commit + push

`strata.controllers.remote_resolution.resolve_remote()` only clones/checks
out a remote (pull direction) — there is no push capability anywhere in
v2 today. New, small addition needed, either as a new function in
`remote_resolution.py` or a dedicated small module
(`strata/integrations/git_push.py` — leaning toward the latter: pushing is
an `Integration`-capability concern, not a `controllers`-layer "resolve a
path" concern, and keeps `remote_resolution.py` focused on its one existing
job):

```python
def push_file(
    repo_path: Path, rendered_file: Path, output_file: str, message: str, *, env: dict[str, str] | None = None
) -> CommandResult:
    """Copy rendered_file to repo_path/output_file, git add, commit, push.

    `env` is whatever `prepare_git_credentials()` (see Credential delivery
    mechanics above) returned for this remote's `AuthenticationModel` —
    `{}` for the ambient/`method: cli` case, unchanged from today's
    `_git_clone()` behaviour. Threaded straight through to every `git`
    subprocess call via `run_command(..., env=env)`.
    """
```

Built entirely on `run_command()` (`strata.utils.transport`) — the same
primitive `TerraformIntegration`/`HelmIntegration` already use for every
subprocess call. No new transport, no new dependency.

**Does `push_file()` have to wait for `SourceIntegration`
(ADR-0021 D9) to exist first? No** — clarifying a structural question the
Authentication section's framing could otherwise leave ambiguous.
`push_file()` is a free function `ArgoCDIntegration`/`FluxIntegration.
deploy()` can call directly today, independent of whether a formal
`SourceIntegration` capability class ever gets built. "One `SourceIntegration`
serving both directions" (Authentication section) describes the
*long-term* ideal home for the credential-setup logic once ADR-0021 D9 is
picked up for its own reasons — it is not a prerequisite this design is
blocked on. If `SourceIntegration` never gets built, `push_file()` still
ships standalone, same as `_git_clone()` already does today.

### The Integration class(es)

One shared base implementing `InfraIntegration` (`plan`/`deploy`/`destroy`),
mirroring v1's `BaseSyncDeployer` split — `ArgoCDIntegration`/
`FluxIntegration` each a thin subclass differing only in `TYPE` and
(optionally, later) which health-check API shape they speak:

- `plan()` -> `git diff --no-index` between the newly-rendered file and
  what's currently in the cloned config repo (no mutation). v1's separate
  `check` step (`git fetch --dry-run`, confirming the remote is reachable)
  has no distinct v2 equivalent here — folded into `plan()` itself (a
  failed `git diff --no-index` against an unreachable/not-yet-cloned repo
  already surfaces as a `plan()` failure; a standalone reachability probe
  before that would be duplicate work for the same information).
- `deploy()` -> `push_file(...)` (copy, add, commit, push).
- `destroy()` -> remove the file from the repo, commit, push.
- `output()` (duck-typed, matching Terraform's own optional method, found
  by reading `deploy_controller.collect_step_outputs()` directly) ->
  **resolved (2026-10-01) — zero changes needed to `collect_step_outputs()`
  itself.** That function is not Terraform-specific at all despite its own
  docstring/comments suggesting so — it is duck-typed purely on the
  *JSON shape* a `CommandResult.stdout` contains
  (`json.loads(stdout)` must be a `dict`, and a key only contributes a
  value when its entry is itself `{"value": ...}`-shaped), never on
  `isinstance(integration, TerraformIntegration)`. So `output(path, *,
  json_format=True, env=env)` just needs to accept that exact call
  signature (ignoring `json_format`/`env` if unneeded) and return a
  `CommandResult` whose `.stdout` is
  `json.dumps({"commit_sha": {"value": sha}, "remote_path": {"value": path}})`
  — the same wrapping shape Terraform's own `terraform output -json`
  already produces. No per-integration-type branch, no `deploy_controller.py`
  changes at all.
- `health` — **not implemented in this design.** The one step that
  actually needs an ArgoCD/Flux API client; deliberately deferred (no
  real-consumer shape to ground a design against, and it's read-only
  status reporting, never load-bearing for the deploy itself).

## Implementation Plan

Four phases, ordered so each is independently testable and none blocks on
a later one — in particular, Phase 4 (real credentials) is **not** a
prerequisite for Phases 1-3, since `method: cli`/ambient auth (today's
`_git_clone()` behaviour, zero config) is a legitimate default the whole
pipeline works correctly with, same-repo case included.

- [x] **Phase 1 — Schema only.** Done 2026-10-01.
  - New `ProvisionerGitOpsModel` (`remote`, `output_file`) in
    `provisioning_model.py`.
  - `ProvisionerModel.gitops: ProvisionerGitOpsModel | None` field + a
    `model_validator` mirroring `validate_source_required_unless_sync()`'s
    own "only recognized tools are checked" leniency.
  - No runtime behaviour change yet — declaring `tool: argocd` still hits
    `IntegrationNotFoundError` at `build run`/`deploy run` until Phase 3,
    same as today. This phase only makes the schema itself accept/reject
    correctly.
  - Tests: schema validation only (`gitops` accepted for `argocd`/`flux`,
    rejected — or simply ignored, matching the leniency rule — for an
    unrecognized custom `tool`; `output_file`/`remote` required when
    `gitops` is set). 6 new tests in `test_models_provisioning.py`, plus
    `test_references.py`'s introspection test updated to include the new
    `spec.provisioners[].gitops.remote` path (found automatically by the
    generic reference walker — `RemoteReference()` metadata works for
    free, no walker changes needed). Full check suite green: mypy (131
    files), ruff, import-linter, pytest (1753 passed, 1 known pre-existing
    unrelated failure).

- [x] **Phase 2 — `push_file()` primitive, ambient auth only.** Done 2026-10-01.
  - New `push_file()` (signature adjusted from the original sketch — takes
    an already-resolved `SolutionRemoteModel` + `root` rather than a bare
    `repo_path`, see below) in a new `strata/integrations/git_push.py`,
    built on `run_command()`.
  - **Evidence found during implementation, not anticipated when this plan
    was written**: `controllers/audit_push.py` (Phase 4 of
    docs/design/audit-trail.md, already shipped) had already solved this
    exact problem — a mutable branch tip that must be fetch+reset-fresh
    before every write — for the audit trail's own git sink. Not reused
    directly (`audit_push.py` lives in `controllers/`, a layer
    `integrations/` must never import, ADR-0003), but its proven
    clone/fetch/reset/add/commit/push sequence and its "never trust an
    existing checkout" `_ensure_checkout()`/`_configure_identity()` shape
    were mirrored closely in the new `git_push.py` instead of
    re-derived — same `git push origin HEAD:<branch>` refspec trick, same
    empty-remote-repo fallback to `origin/HEAD`, same local (not global)
    git identity via `resolve_actor()` (`utils/actor.py`, already
    importable from `integrations/`).
  - This also means `push_file()`'s real signature ended up resolving its
    own checkout (clone/fetch/reset), not just "copy, add, commit, push"
    against an already-fresh `repo_path` as first sketched — a bare
    `repo_path` parameter would have pushed the freshness problem onto
    whichever Phase 3 caller invokes it, silently reopening the exact
    staleness bug `audit_push.py`'s own docstring already names. Added
    `layout.gitops_push_checkout_path(root, remote, branch)`, its own
    `gitops-push/` subdirectory so this checkout population can never
    collide with `remote_checkout_path()`'s read checkouts or
    `audit_push_checkout_path()`'s audit-push checkouts.
  - Returns a `PushResult` (`success`, `detail`) rather than a raw
    `CommandResult` — never raises, matching `audit_push.PushResult`'s own
    reasoning (a caller decides whether a failed push fails the deploy).
  - `env` parameter accepted and threaded through every git subprocess
    call, but unused until Phase 4 — identical to `_git_clone()`'s current
    zero-config behaviour, no regression.
  - Tests (`tests/strata/integrations/test_integrations_git_push.py`): 4
    mocked-`run_command` tests (non-git remote type rejected, missing
    `reference` rejected, the full clone/fetch/reset/add/commit/push
    sequence runs, `env` is threaded through every call) + 3 real
    end-to-end tests against a real local bare git repo (`git init
    --bare`) — round-trip push verified by an independent clone, a
    same-content second push is a true no-op (no new commit), and the
    checkout path matches `layout.gitops_push_checkout_path()`. Full check
    suite green: mypy (132 files), ruff, import-linter (1 kept, 0 broken),
    pytest (1760 passed, 1 known pre-existing unrelated failure).

- [x] **Phase 3 — The Integration classes, wired end to end.** Done 2026-10-01.
  - `BaseGitOpsIntegration` (shared `plan`/`deploy`/`destroy`/`output`)
    + `ArgoCDIntegration`/`FluxIntegration` thin subclasses (`TYPE` only
    differs) in a new `strata/integrations/gitops.py`. `COMMAND = "git"`
    — a correction found while implementing: `Integration.is_available()`
    treats a `None` command as *unavailable*, so `deploy_controller.py`'s
    own preflight would otherwise always reject this tool; `git` is the
    real external dependency anyway (no `argocd`/`flux` CLI is ever
    invoked — `plan`/`deploy`/`destroy` call `git_push.py` directly, not
    `self.run()`).
  - Registered in `strata.integrations.registry._KNOWN` (`"argocd"`,
    `"flux"`) — neither was actually in `_KNOWN_V1_TYPES`'s hint list to
    begin with (checked directly; this Plan's earlier wording assumed so,
    incorrectly), so nothing needed removing there.
  - **This phase's "no build_controller.py/deploy_controller.py changes
    expected" claim turned out only partly true — found only by reading
    `deploy_controller.py`'s actual dispatch loop directly, not by
    re-trusting the Overview's earlier, shallower check.** The Overview's
    "both already handle a source-less provisioner's *path*" finding is
    correct but incomplete: the generic (non-container) dispatch loop
    calls `integration.plan(path, out_file=..., env=env)` /
    `.deploy(path, plan_file=..., env=env)` with **only** `path` and
    `env` — never `provisioner`, never a resolved remote — so there is no
    way for `plan`/`deploy`/`destroy` to know *which* remote/output_file
    to push to without some other channel. Fix: `prepare()` (called
    during `build run`, which **does** have everything) resolves
    `provisioner.gitops.remote` against a `remotes` mapping and writes a
    small `.gitops-push.json` sidecar into `path` — the step's on-disk
    build directory, which (unlike process memory) survives between the
    separate `build run`/`deploy run` CLI invocations. `plan`/`deploy`/
    `destroy`/`output` read it back from `path` alone. The one real
    change needed: `build_controller.py`'s single existing
    `integration.prepare(...)` call site gained `remotes=remotes,
    root=context.root` — both accepted for free by `InfraIntegration.
    prepare()`'s pre-existing `**kwargs: Any`, so every other
    integration's `prepare()` call is unaffected. `deploy_controller.py`
    itself needed zero changes, since the sidecar travels via `path`
    instead of a new parameter there.
  - `default_output()` override raises `IntegrationError` when
    `output.template` is unset (docs/design/gitops-integration.md's own
    "Rendering" conclusion: no sensible default projection exists for a
    GitOps push) — the honest-default rule this Plan already called for.
  - `git_push.py` gained two Phase-3-driven additions: `remove_file()`
    (destroy()'s primitive, mirrors `push_file()`) and a public
    `ensure_checkout()` (plan()'s/output()'s read-only primitive — a
    fresh checkout with no write). `push_file()`/`remove_file()` were
    refactored to share one `_commit_and_push()` tail and one
    `_validate_remote()` guard — behaviour-preserving (all 7 Phase 2
    tests still pass unchanged).
  - `plan()` normalises `git diff --no-index`'s exit codes 0 (identical)
    and 1 (different — including "new file") to a *successful*
    `CommandResult` either way (diff text in `stdout`) — only 2+ is a
    real failure. Required because `deploy_controller.py`'s generic loop
    only ever checks `.is_successful` (`returncode == 0`), unlike
    `TerraformIntegration`'s own `detailed_exitcode` callers, which
    inspect `.returncode` directly by their own documented convention —
    propagating git diff's raw exit code 1 here would have made every
    real "there are changes to push" plan look like a failure.
  - `output()` re-derives everything from the sidecar + a fresh
    `ensure_checkout()` rather than trusting anything `deploy()` left in
    memory, since `collect_step_outputs()` calls it as a wholly separate
    call — returns `{"commit_sha": {"value": <40-char SHA>}, "remote_path":
    {"value": <output_file>}}`, the exact shape confirmed against
    `collect_step_outputs()`'s own duck-typing earlier in this doc.
  - `test_integrations_capabilities.py`'s guardrail tests needed updating
    (found only by running the full suite, not anticipated): the
    resolved-value-delivery guardrail now has an explicit, commented
    exception for the three new classes (`output.template` *is* their
    only delivery mechanism, enforced by the `default_output()` raise
    above — matches that test's own "needs an explicit, commented
    exception, not a silent pass" rule), and the known-integration-names
    guardrail's expected set grew to include them.
  - Tests: `tests/strata/integrations/test_integrations_gitops.py` (16
    tests — class contract, `default_output()`/`prepare()` validation
    including the dry-run-skips-sidecar case, `plan`/`deploy`/`destroy`/
    `output` failing cleanly with no sidecar, and one real end-to-end
    `prepare()` -> `plan()` -> `deploy()` -> `output()` -> `plan()` again
    -> `destroy()` cycle against a real local bare git repo). Full check
    suite green: mypy (133 files), ruff, import-linter (1 kept, 0
    broken), pytest (1776 passed, 1 known pre-existing unrelated
    failure).

- [x] **Phase 4 — Real credentials (`SSHKeyAuthenticationModel`/
  `TokenAuthenticationModel`).** Done 2026-10-01.
  - The two new `AuthenticationModel` methods (`ssh_key`/`token`) +
    `method_fields`/`method` Literal updates, in `auth_models.py`. 11
    tests in `test_models_auth.py` (6 new, matching the file's own
    established accepted/missing-config/mismatched-config pattern).
  - `prepare_git_credentials()` (`strata/integrations/git_push.py`) —
    `GIT_SSH_COMMAND` + a `0600` temp keyfile for `ssh_key` (optional
    `known_hosts` pin, else `StrictHostKeyChecking=accept-new` so a
    non-interactive run never hangs on a prompt); `GIT_ASKPASS` + a
    generated helper script (`.bat` on Windows, `#!/bin/sh` + `chmod 700`
    otherwise — `GIT_ASKPASS` must be directly executable, and Windows
    has no shebang support) for `token`; `({}, no-op)` for `cli`/`None`/
    unset, today's ambient behaviour exactly unchanged. Takes an
    already-resolved `resolved_values: Mapping[str, str]` alongside the
    `AuthenticationModel` — it only does the key-reference-name ->
    env-shape translation; resolving a key reference to its real secret
    value is a controller-layer concern this function deliberately
    doesn't reach for (same `integrations/` layering constraint Phase
    2/3 already worked within).
  - Wired into `push_file()`/`remove_file()`/`ensure_checkout()`
    (`git_push.py`) via a new shared `credential_env()` context manager
    (guarantees the temp keyfile/script cleanup callback always runs,
    success or failure) and into `remote_resolution._git_clone()`/
    `resolve_remote()` (`controllers/`, which may import this
    `integrations/`-layer function — ADR-0003 allows a higher layer
    importing a lower one). Every new parameter (`auth`,
    `resolved_values`) is optional and defaults to `None`, so every
    existing caller's behaviour is provably unchanged (confirmed: all
    pre-Phase-4 tests across both files still pass unmodified).
  - Tests: `prepare_git_credentials()` unit coverage (8 tests —
    `None`/`cli` -> `{}`; `ssh_key` writes a keyfile with `0600`
    permissions — checked directly via `stat.S_IMODE()` on POSIX — and
    cleans it up after; `known_hosts` wired in when given; a missing key
    reference raises `IntegrationError`; same shape for `token`). The
    live round-trip the Plan allowed skipping turned out feasible without
    a real git-over-SSH fixture at all: the generated `GIT_ASKPASS`
    script is invoked directly (exactly as git itself would — one
    positional prompt argument, the token delivered only via its own env
    var) and asserted to print the real token back — a genuine, not
    merely constructed-string, assertion. Plus 2 wiring tests
    (`push_file()` threads `GIT_SSH_COMMAND` through every git call when
    `auth`/`resolved_values` are given; `resolve_remote()`/`_git_clone()`
    likewise). Full check suite green: mypy (133 files), ruff,
    import-linter (1 kept, 0 broken), pytest (1792 passed, 1 known
    pre-existing unrelated failure).
  - **Explicitly not required for this phase, confirmed still true**: a
    full `SourceIntegration` capability class (ADR-0021 D9) —
    `prepare_git_credentials()` is called directly by
    `push_file()`/`remove_file()`/`ensure_checkout()`/`_git_clone()`,
    same "ships independently" reasoning as Phase 2's own note.
  - **One real, bounded gap this phase does NOT close, found and flagged
    rather than silently left implicit**: `BaseGitOpsIntegration.plan()`/
    `.deploy()`/`.destroy()` (Phase 3) have no path to a real `auth`+
    `resolved_values` at actual deploy time — `deploy_controller.py`'s
    generic (non-container) dispatch loop never resolves a remote's
    `integration` field or passes deploy-scoped secret values down to
    `plan`/`deploy`/`destroy` for *any* tool (confirmed directly: the
    same gap Phase 3 found and closed for `provisioner`/`remotes`, but
    doing so again here for authentication credentials is additional,
    separable scope — secrets are deploy-time-only (ADR-0022 D4), so
    unlike Phase 3's sidecar fix, this one genuinely cannot be
    pre-resolved at `build run` time and persisted to disk without
    leaking the secret into a reviewable build artifact). Closing it
    needs `build_controller.py`/`deploy_controller.py` to resolve
    `remote.integration` -> `IntegrationModel.spec.authentication` and
    thread `resolved.values` into the generic `plan`/`deploy`/`destroy`
    calls — real, scoped, surgical work in the same vein as Phase 3's own
    fix, deliberately left for a follow-up once a real consumer needs a
    non-ambient GitOps remote (same "needs a real consumer to validate"
    discipline the Authentication section's own first-design-pass note
    already applied to the method shapes themselves).

- [x] **Phase 5 — Wire real credentials into an actual `deploy run`.**
  Closes Phase 4's own flagged gap. Designed 2026-10-01, reviewed and
  corrected 2026-10-02 (the review below), implemented 2026-10-02.

  **Simpler than Phase 4's own gap description implied, found only by
  reading `deploy_controller.py`'s per-step loop scope directly rather
  than assuming a sidecar is the only available mechanism:** that loop
  already iterates `(step, provisioner, integration)` tuples and already
  has `remotes: dict[str, SolutionRemoteModel]` + `index` (the
  `DocumentIndex`) in scope at exactly the point `plan`/`deploy` are
  called — so `remote.integration` can be resolved to a real
  `IntegrationModel.spec.authentication` **fresh, at actual deploy time**,
  with no build-time involvement and no sidecar at all. This is strictly
  better than persisting anything at build time: an `Integration`
  document's `authentication` could change between `build run` and
  `deploy run`, and resolving it fresh is also simply the honest
  reflection of "secrets are deploy-time-only" (ADR-0022 D4) — nothing
  about auth resolution belongs at build time to begin with, unlike
  Phase 3's `remote`/`output_file`, which *do* need to survive the
  build/deploy process boundary because nothing re-derives them at
  deploy time otherwise.

  - **`deploy_controller.py`** (the only real controller change): inside
    the existing per-step loop, **only when `provisioner.gitops is not
    None`**, resolve `remotes.get(provisioner.gitops.remote)` and, if it
    names an `integration`, look it up via `index.get(PlatformKind.
    INTEGRATION, ...)` (same pattern `integration_resolution.
    resolve_integration()` uses for `provisioner.integration` — but
    **not** its strict `UsageError`-on-missing behaviour: that's earned
    there by Phase 1 validating `provisioner.integration` exists
    — `SolutionRemoteModel.integration` has no `References(...)`
    annotation, confirmed directly, so it is schema-declared but never
    existence-checked; a missing/typo'd name here must silently resolve
    `auth = None`, i.e. fall back to today's ambient behaviour, not
    raise) to get its `IntegrationModel.spec.authentication`. For every
    *other* step (no `gitops`, or no `remote.integration` set), `auth`
    and `resolved_values` both stay `None` — **not** "the real values,
    but harmless" — see why this distinction is load-bearing, not
    cosmetic, below.
  - **`collect_step_outputs()`** (same file) — **corrected on review,
    this is the one real risk the original framing glossed over**: this
    function's `output(path, json_format=True, env=env)` call is the
    *only* currently-reachable consumer of a tool's `output()` in
    production (Helm has no `output()` at all; Compose's has a
    completely different shape *and* is `Capability.CONTAINER`, so the
    loop's container branch already `continue`s before ever reaching this
    call — confirmed by reading the loop directly, not assumed). That
    leaves `TerraformIntegration.output()` as the real, sole consumer
    today, and — checked directly — **it has no `**kwargs: Any`**, unlike
    its own sibling `plan()`/`deploy()` (which do). Unconditionally
    passing `auth=`/`resolved_values=resolved.values` into this call for
    *every* step (not just GitOps ones) would make every Terraform step's
    `output()` call raise `TypeError` — silently caught by this
    function's own existing `except TypeError: return {}`, degrading
    **every real Terraform deployment's** `${output:...}` cross-step
    token resolution to permanently empty. Not a crash; a silent
    regression, exactly the failure class this whole design discipline
    exists to catch before it ships. **Fix**: only add `auth`/
    `resolved_values` to the call when at least one is not `None` (i.e.
    only for an actual GitOps step) — Terraform's own call stays
    textually `output(path, json_format=True, env=env)`, provably
    unchanged. Belt-and-suspenders: also give `TerraformIntegration.
    output()` a `**kwargs: Any` catch-all, matching its own `plan`/
    `deploy` siblings — a small, independently-correct consistency fix,
    not load-bearing on its own once the conditional above is in place.
  - **`gitops.py`** — **corrected on review**: not quite "already read out
    of existing `**kwargs`" as first described. `plan()`/`deploy()`/
    `destroy()` already use a `**kwargs: Any` + `kwargs.get("env")` style
    and need one added line each (`kwargs.get("auth")`/`kwargs.get(
    "resolved_values")`, forwarded to `ensure_checkout()`/`push_file()`/
    `remove_file()`). `output()` is the one that needs a real change, not
    just a read: it currently does `del json_format, kwargs` —
    discarding everything — so it needs new explicit `auth`/
    `resolved_values` kwonly params (matching its own existing `env`
    style, rather than the other three methods' `**kwargs.get(...)`
    style) actually threaded into its own `ensure_checkout()` call. Still
    **zero new credential logic** — Phase 4's `ensure_checkout()`/
    `push_file()`/`remove_file()` already do the real
    `prepare_git_credentials()` work; this phase only carries the values
    there.
  - **Not in scope for this phase** (separate, pre-existing gaps, neither
    created nor worsened by this work): there is still no `deploy
    destroy`/`strata destroy run` command wired to call
    `InfraIntegration.destroy()` for *any* tool, GitOps included — so
    `destroy()`'s own credential wiring can't be exercised end-to-end
    until that command exists; the plumbing above still reaches it
    identically whenever that command is eventually built, nothing
    GitOps-specific to redo then. A full `SourceIntegration` capability
    class (ADR-0021 D9) remains unbuilt, same as every earlier phase.
  - **Implemented 2026-10-02, exactly per the corrected design above**:
    `TerraformIntegration.output()` gained `**kwargs: Any`.
    `deploy_controller.py`'s per-step loop computes `auth`/
    `step_resolved_values` conditionally (`None`/`None` unless
    `provisioner.gitops is not None`) right before the existing
    `plan`/`deploy` calls, threads them through, and passes the same pair
    to `collect_step_outputs()`, which only adds them to its own
    `output(...)` call when at least one is non-`None`. `gitops.py`'s
    `plan()`/`deploy()`/`destroy()` pull `auth`/`resolved_values` out of
    `**kwargs` (one added line each); `output()` gained real explicit
    `auth`/`resolved_values` kwonly params (it previously discarded
    `**kwargs` outright) — all four forward unchanged into
    `ensure_checkout()`/`push_file()`/`remove_file()`, no new credential
    logic. 15 new tests: `test_deploy_controller.py` (5 — the conditional
    `collect_step_outputs()` behaviour, plus a regression test that would
    have failed before the `TerraformIntegration.output()` fix), `test_
    integrations_gitops.py` (4 — `plan`/`deploy`/`destroy`/`output` each
    forward `auth`/`resolved_values` to their respective `git_push.py`
    primitive, confirmed via monkeypatching), `test_integrations_
    terraform.py` (1 — the exact regression the review found, asserted
    directly against the real method). Full check suite green: mypy (133
    files), ruff, import-linter (1 kept, 0 broken), pytest (1801 passed,
    1 known pre-existing unrelated failure). All 5 Implementation Plan
    phases are now complete; `tool: argocd`/`flux` works end to end today,
    ambient or with real `ssh_key`/`token` credentials resolved fresh at
    deploy time.

## Related Decisions

- [ADR-0009](../decisions/0009-module-model-design-decisions.md) — unified
  `ProvisionerType` + `SYNC_PROVISIONER_TYPES`/`WORKLOAD_DEPLOYER_TYPES`,
  the schema groundwork this design builds on without any changes needed
  there.
- [ADR-0021](../decisions/0021-integration-layer.md) D9 — `Capability.
  SOURCES`/`SourceIntegration`, already named as a real, scheduled future
  capability (not speculative) — this design's "Authentication" section
  sketches it serving both fetch and push.
- [ADR-0021](../decisions/0021-integration-layer.md) — the `Integration`/
  `InfraIntegration` capability split this design's classes implement; D1's
  CLI-vs-API transport split doesn't apply here (git is the only
  transport this needs).
- [ADR-0023](../decisions/0023-build-output-rendering.md) D3 — `output.
  template`/`default_output()`, already tool-agnostic and reused here
  unchanged.
- [docs/design/remotes.md](remotes.md) — `resolve_remote()`'s current
  pull-only scope; this design's `push_file()` is the first push-direction
  primitive.
- [docs/design/deploy-command.md](deploy-command.md) — Remaining Work item
  2 (cross-step outputs), whose `collect_step_outputs()` this design's
  `output()` method needs to stay compatible with.
- `src/strata/models/auth_models.py` — `AuthenticationModel`'s existing
  closed `method` vocabulary and its "key references, never inline
  secrets" convention, which `SSHKeyAuthenticationModel`/
  `TokenAuthenticationModel` extend rather than replace.
- `src/strata/models/provisioning_model.py` —
  `ProvisionerAnsiblePropertiesModel.ssh_private_key_secret`, the real,
  already-shipped precedent for "a secret-reference field holding an SSH
  private key" this design's `SSHKeyAuthenticationModel.private_key`
  mirrors (schema-only today — no Ansible integration consumes it yet
  either, same unimplemented-but-real-shape category as this design).

## Remaining Work / Open Questions

1. ~~No real consumer evidence yet.~~ **No issue (2026-10-01) — stays
   explicitly deferred/unscheduled, as already framed.** Haven's real
   `workspace.yaml` only declares `terraform`/`helm` provisioners; a v2 of
   `cfg-int-deployment` is reportedly in progress and may become the real
   trigger, but that isn't independently confirmed yet (checked its git
   history directly — no GitOps-related commits/branches exist there
   today). Re-verify against its real manifests once that work lands.
2. ~~Rendered file shape is undecided.~~ **Resolved (2026-10-01)** — see
   the Rendering section above: confirmed against v1's real
   `builders/sync_builder.py` that the rendered file is always a plain
   values/config file (`output_file`-shaped), never the ArgoCD
   `Application`/Flux `HelmRelease`/`Kustomization` CR itself (that's a
   one-time bootstrap resource, out of scope for `build run`/`deploy run`
   entirely). `build_run()`'s already-resolved `graph` +
   `output.template` is already sufficient to render it — no new
   rendering capability needed.
3. ~~`collect_step_outputs()` compatibility.~~ **Resolved (2026-10-01)** —
   see the `output()` bullet in "The Integration class(es)" above: read
   `collect_step_outputs()` directly and found it is duck-typed purely on
   JSON shape (`{key: {"value": ...}}`), never on the integration's type —
   a GitOps `output()` returning that same shape needs **zero changes** to
   `collect_step_outputs()`/`deploy_controller.py` at all. Turned out
   simpler than the original "needs a per-integration-type branch" framing
   assumed.
4. **Push credentials/auth — real, larger prerequisite, not scoped to this
   design alone, now has a first concrete design.** Expanded into its own
   "Authentication" + "Credential delivery mechanics" sections above
   (2026-10-01): neither fetch nor push authenticates through strata today
   (ambient git config only, confirmed for both); the right shape if/when
   built is **one** `SourceIntegration` serving both directions; same-repo
   push can lean on ambient identity, cross-repo (the realistic GitOps
   topology) cannot and needs a real credential. First-pass schema
   sketched: `SSHKeyAuthenticationModel`/`TokenAuthenticationModel` added
   to `AuthenticationModel`'s existing closed vocabulary (not a separate
   git-only model), plus a `prepare_git_credentials()` sketch for getting
   the resolved value to the `git` subprocess without an `argv` leak
   (`GIT_SSH_COMMAND`+temp keyfile / `GIT_ASKPASS`+env var, both via
   `run_command()`'s existing `env=` kwarg). **Still not implemented or
   scheduled** — this is a real, buildable design now, not a decision to
   build; needs a real consumer to validate the two method shapes before
   committing to them in code. Wiring real credentials remains ADR-0021
   D9's job, shared with every other remote use, not unique to GitOps.
5. ~~`health` (ArgoCD/Flux API client).~~ **Confirmed not needed
   (2026-10-01)**, per direct note ("not needed? if commit/push is ok.").
   Read-only, never load-bearing for the actual deploy — stays deferred
   indefinitely, no trigger to revisit.

## Changelog

- 2026-10-01: Created, after direct challenge ("do we need an integration?
  ... they get deployed on AKS, strata pushes a new helm values file...
  they see the new push and redeploy right?") was confirmed against v1's
  real `deployers/sync_deployer.py` — the mechanism is git commit+push,
  not a Kubernetes/ArgoCD/Flux API call. Scoped a v2 design: one new typed
  `ProvisionerModel.gitops` field (mirroring `backend`/`properties`'s
  existing per-tool-field precedent), reuse of already-built
  `output.template`/`render_output_template()` for rendering, one new
  small `push_file()` primitive (the only genuinely missing piece —
  `resolve_remote()` only ever pulls today), and a thin `InfraIntegration`
  subclass pair for ArgoCD/Flux differing only in `TYPE`. Explicitly not
  scheduled — zero real consumer evidence found (checked haven directly);
  `health`/rendered-file-shape left as open, evidence-gated questions
  rather than guessed at.
- 2026-10-01: Added a worked YAML example (`tool: argocd` provisioner,
  no `source`, `gitops.remote`/`.output_file` + `output.template`) under
  the schema section. Updated Remaining Work item 1 per direct note: a v2
  of `cfg-int-deployment` is currently being worked on — recorded as a
  possible real-evidence trigger, explicitly flagged as not yet
  independently verified (checked that repo's real git history directly;
  no GitOps-related commits/branches exist there yet) — re-check its real
  manifests once that work actually lands, rather than scheduling this
  design off the expectation alone.
- 2026-10-01: Resolved 3 of 5 open questions on direct review. (1) No
  issue — stays deferred/unscheduled as already framed. (2) Rendered file
  shape: confirmed against v1's real `builders/sync_builder.py` (not
  previously read in detail) that the rendered file is always a plain
  values/config file, never the ArgoCD `Application`/Flux `HelmRelease`/
  `Kustomization` CR itself — that CR is a one-time bootstrap resource,
  entirely out of scope for `build run`/`deploy run`. Confirms
  `build_run()`'s existing `graph` + `output.template` is already
  sufficient; no new rendering capability needed. (5) `health` confirmed
  not needed, stays deferred indefinitely. (4) Investigated directly
  ("see integration.auth / how do solution.spec.remotes auth?") and found
  a real, larger prerequisite: `remote_resolution._git_clone()`'s own
  docstring confirms **no remote — read or write — authenticates through
  strata's `Integration` mechanism today**; `SolutionRemoteModel.
  integration` is schema-declared but unwired (`Capability.SOURCES` has no
  ABC yet, ADR-0021 D9, already remotes.md's own tracked Remaining Work).
  `push_file()` can inherit the same ambient-auth assumption `_git_clone()`
  already makes without regressing anything, but real credential wiring
  is a shared prerequisite, not unique to this design. (3) Left open per
  direct note ("more info needed") — `collect_step_outputs()`
  compatibility still needs investigation.
- 2026-10-01: Resolved the remaining open question (3). Read
  `deploy_controller.collect_step_outputs()` directly: it is duck-typed
  purely on JSON shape (`{key: {"value": ...}}`), never on the
  integration's type — despite its own docstring/comments framing it as
  Terraform-specific. A GitOps `output()` returning `{"commit_sha":
  {"value": sha}, "remote_path": {"value": path}}` needs **zero changes**
  to `collect_step_outputs()`/`deploy_controller.py`. All 5 original open
  questions are now either resolved, confirmed-not-needed, or explicitly
  deferred with a clear trigger — only real consumer evidence (item 1) and
  the shared ADR-0021 D9 credential-wiring prerequisite (item 4) remain as
  genuine blockers to implementation, neither of which is this design's
  own gap to close.
- 2026-10-01: Expanded item 4 into a full "Authentication" design section,
  per direct request ("let's look at how we would authenticate for both
  types of repos"). Key findings: neither fetch nor push authenticates
  through strata today (both ambient-only, confirmed by reading
  `_git_clone()` directly); the right shape is **one** `SourceIntegration`
  serving both directions via `Capability.SOURCES` (already named in
  ADR-0021 D9 as a real, scheduled capability, not speculative) — fetch
  and push differ only in which git subcommand runs at the end, not in
  how credentials are set up; and `AuthenticationModel` has **no
  git-native auth method in either v1 or v2** (confirmed by reading both
  models directly — not a v2 regression) — `method: cli` is the closest
  honest fit for today's ambient reality, a dedicated SSH-key/PAT method
  is deliberately left undesigned until a real private remote forces the
  question. Added ADR-0021 D9 as its own Related Decisions entry.
- 2026-10-01: Added the same-repo-vs-cross-repo distinction to the
  Authentication section, per direct question ("can we assume the SPN
  running the pipeline/CLI has access to that git thing, but that might
  not be taken over during the runtime in earlier projects you needed to
  get the github_token for instance?"). Confirmed: ambient identity is
  plausible for same-repo push (v2's own `ci-release.yml`/`ci-docs.yml`
  already do exactly this with GitHub Actions' auto-`GITHUB_TOKEN`), but
  essentially never works for cross-repo push — the realistic GitOps
  topology, since a GitOps config repo is normally shared across many
  apps, not per-app. This is a hard GitHub platform boundary for
  `GITHUB_TOKEN` specifically (cryptographically scoped to the triggering
  repo, not a configuration choice); Azure DevOps SPNs can be granted
  cross-repo access but only via deliberate provisioning, never
  automatically. Matches real prior-project experience directly. Cited
  `AzureKeyVaultResolver`'s existing `DefaultAzureCredential` chained-
  fallback pattern (ambient first, explicit credential as override) as
  the shape `SourceIntegration` should copy. Raised the priority framing
  on the dedicated PAT/deploy-key auth method: no longer a deferred edge
  case for GitOps specifically, since cross-repo is the realistic case,
  not the exception — still not designed here, same discipline as before.
- 2026-10-01: First concrete auth-method design, per direct request ("add
  to the doc, and look into first design for the auth methods"). Added
  `SSHKeyAuthenticationModel`/`TokenAuthenticationModel` sketches,
  extending `AuthenticationModel`'s existing closed `method` vocabulary
  rather than inventing a separate git-only auth model (matches
  `SolutionRemoteModel`'s own stated "Integration is strata's single
  credential mechanism" philosophy) — found and cited a real, already-
  shipped precedent for the shape, `ProvisionerAnsiblePropertiesModel.
  ssh_private_key_secret` (schema-only, same as this design). Added a new
  "Credential delivery mechanics" section: embedding a token in the clone/
  push URL is explicitly ruled out (leaks via `argv`, the same class of
  leak `run_command()`'s own docstring already warns about) in favor of
  `GIT_SSH_COMMAND`+temp keyfile (SSH) / `GIT_ASKPASS`+env var (token),
  both via `run_command()`'s existing `env=` kwarg — no new transport.
  Sketched `prepare_git_credentials()` as the one shared function both
  `_git_clone()` and `push_file()` would call. Explicitly still
  unimplemented and unscheduled — a real, buildable design now, not a
  decision to build it.
- 2026-10-01: Full-document review, per direct request. Found and fixed 5
  real issues: (1) the worked YAML example's comment pointed at a "Push
  credentials" section that no longer exists under that name (renamed to
  Authentication across earlier edits) — retargeted. (2) `push_file()`'s
  own signature never actually accepted the credential `env` the
  Credential delivery mechanics section describes producing — added an
  `env` parameter and wired the connection explicitly. (3) Added a
  clarifying note that `push_file()` ships independently of whether
  `SourceIntegration`/ADR-0021 D9 ever gets built — the Authentication
  section's "one `SourceIntegration`" framing is the long-term ideal, not
  a prerequisite this design is blocked on. (4) Noted that v1's `check`
  step (`git fetch --dry-run`) has no distinct v2 equivalent — folded into
  `plan()`, since a failed `plan()` against an unreachable repo already
  surfaces the same information. (5) Tightened the top `Status:` line —
  "fully specified" was accurate for the core mechanism but overstated the
  Authentication section, which is explicitly a first design still needing
  a real consumer to validate before being final.
- 2026-10-01: Added an Implementation Plan — 4 phases, ordered so none
  blocks on a later one (`method: cli`/ambient auth is a legitimate
  default the whole pipeline works with, so Phase 4's real credentials
  are not a Phases 1-3 prerequisite). Phase 1: schema only
  (`ProvisionerGitOpsModel` + the `gitops` field + validator). Phase 2:
  `push_file()`, ambient auth only, tested against real local bare git
  repos (no network needed). Phase 3: the `ArgoCDIntegration`/
  `FluxIntegration` classes, registered and wired end to end — expected to
  mostly *prove* the Overview's "build_run()/deploy_run() already handle
  this correctly" finding rather than add new orchestration code. Phase 4:
  `SSHKeyAuthenticationModel`/`TokenAuthenticationModel` +
  `prepare_git_credentials()`, wired into both `push_file()` and
  `_git_clone()` — the first point fetch and push actually share
  executable logic, not just a conceptual capability. Still not scheduled
  — the plan exists so a real pickup has a concrete starting sequence,
  not because implementation is starting now.
- 2026-10-01: Implemented Phase 1 (schema only). New `ProvisionerGitOpsModel`
  (`remote: Annotated[PlatformName, RemoteReference()]`, `output_file: str`,
  both required) and `ProvisionerModel.gitops: ProvisionerGitOpsModel | None`
  in `provisioning_model.py`, plus `validate_gitops_only_for_sync_tools()`
  mirroring `validate_properties_only_for_ansible()`'s "reject only a
  recognized mismatch, allow an unrecognized/custom tool through" leniency.
  6 new tests in `test_models_provisioning.py`. One incidental find:
  `test_references.py`'s introspection test needed its expected path set
  updated to include `spec.provisioners[].gitops.remote` — the generic
  reference walker picked up the new `RemoteReference()`-annotated field
  automatically, with zero walker changes, confirming D9's "works for free"
  claim in practice. Full check suite green (mypy 131 files, ruff,
  import-linter, pytest 1753 passed / 1 known pre-existing unrelated
  failure). No runtime behaviour change — `tool: argocd` still hits
  `IntegrationNotFoundError` until Phase 3.
- 2026-10-01: Implemented Phase 2 (`push_file()` primitive, ambient auth
  only). Found, while implementing, that `controllers/audit_push.py`
  (already shipped, a different design doc's Phase 4) had already solved
  this exact "mutable branch tip, must be fresh before every write"
  problem for the audit trail's own git sink — its proven
  clone/fetch/reset/add/commit/push sequence was mirrored closely in a new
  `strata/integrations/git_push.py` rather than re-derived from scratch,
  not reused directly (layering forbids `integrations/` importing
  `controllers/`). This changed `push_file()`'s real signature from the
  original sketch: it now resolves its own fresh checkout (clone if
  absent, fetch + reset) given an already-resolved `SolutionRemoteModel` +
  `root`, rather than assuming a `repo_path` some other, unspecified caller
  already keeps fresh — the original sketch would have silently reopened
  the staleness bug `audit_push.py` exists to avoid. Added
  `layout.gitops_push_checkout_path()`, its own `gitops-push/`
  subdirectory so this checkout population can never collide with
  `remote_checkout_path()`'s or `audit_push_checkout_path()`'s. 7 new
  tests (`test_integrations_git_push.py`) — 4 mocked, 3 real end-to-end
  against a local bare git repo. Full check suite green (mypy 132 files,
  ruff, import-linter, pytest 1760 passed / 1 known pre-existing unrelated
  failure). No `Integration` class yet — `tool: argocd` still hits
  `IntegrationNotFoundError` until Phase 3; `push_file()` is a
  standalone, tested primitive ready for Phase 3 to call.
- 2026-10-01: Implemented Phase 3 (the Integration classes, wired end to
  end). The real, load-bearing finding, made only by reading
  `deploy_controller.py`'s actual dispatch loop directly rather than
  re-trusting this Plan's own earlier "no controller changes expected"
  claim: `plan`/`deploy`/`destroy` are called generically with only
  `path`+`env`, never `provisioner`/a resolved remote — so a sidecar
  (`.gitops-push.json`, written by `prepare()` into the step's on-disk
  build directory, which survives between the separate `build run`/
  `deploy run` processes unlike anything held in memory) is what actually
  carries `gitops.remote`'s resolution forward, not a new parameter deploy
  time never had available anyway. The one real, surgical controller
  change this took: `build_controller.py`'s single `integration.
  prepare(...)` call site gained `remotes=remotes, root=context.root`
  (absorbed for free by every other tool's inherited `**kwargs: Any`).
  `deploy_controller.py` itself needed zero changes. New `strata/
  integrations/gitops.py` (`BaseGitOpsIntegration`, `ArgoCDIntegration`,
  `FluxIntegration`, `COMMAND = "git"` — found necessary because
  `Integration.is_available()` treats a `None` command as unavailable,
  which would have failed `deploy_controller.py`'s own preflight check
  for this tool otherwise). `git_push.py` gained `remove_file()` and a
  public `ensure_checkout()` for `destroy()`/`plan()`/`output()`,
  refactored behind a shared `_commit_and_push()`/`_validate_remote()`
  without changing `push_file()`'s own behaviour (all 7 Phase 2 tests
  still pass unchanged). `plan()` normalises `git diff --no-index`'s
  exit codes 0/1 to a successful `CommandResult` either way — required
  because the generic dispatch loop only checks `.is_successful`, unlike
  `TerraformIntegration`'s own documented `detailed_exitcode` convention.
  Registered in `registry._KNOWN` (`"argocd"`, `"flux"`) — neither was
  actually in `_KNOWN_V1_TYPES`'s hint list as this Plan's Phase 3 bullet
  had assumed; checked directly, nothing needed removing there. Running
  the full suite (not just the new file) surfaced two guardrail tests in
  `test_integrations_capabilities.py` that needed updating — an explicit,
  commented exception for the GitOps classes in the resolved-value-
  delivery guardrail (`output.template` *is* their only delivery
  mechanism, enforced by `default_output()`'s own raise), and the
  known-integration-names guardrail's expected set. 16 new tests
  (`test_integrations_gitops.py`), including one real end-to-end
  `prepare()` -> `plan()` -> `deploy()` -> `output()` -> `plan()` again
  -> `destroy()` cycle against a real local bare git repo. Full check
  suite green (mypy 133 files, ruff, import-linter, pytest 1776 passed /
  1 known pre-existing unrelated failure). `tool: argocd`/`flux` now
  works end to end for an ambiently-authenticated or publicly-writable
  remote — only Phase 4 (real credentials) remains.
- 2026-10-01: Implemented Phase 4 (real credentials). New
  `SSHKeyAuthenticationModel`/`TokenAuthenticationModel` on
  `AuthenticationModel`'s closed `method` vocabulary, exactly as the
  "First design pass" sketch already specified. New
  `prepare_git_credentials()` in `git_push.py`, deliberately taking an
  already-resolved `resolved_values: Mapping[str, str]` alongside the
  `AuthenticationModel` rather than resolving key references itself —
  that resolution is a controller-layer concern (`value_controller.
  resolve_values()`'s whole job) this `integrations/`-layer function
  cannot reach, the same layering constraint already respected
  throughout Phases 2-3. Wired into `push_file()`/`remove_file()`/
  `ensure_checkout()` via a new `credential_env()` context manager (keeps
  the mandatory cleanup-always-runs guarantee out of three repeated
  try/finally blocks) and into `remote_resolution._git_clone()`/
  `resolve_remote()` — both gained optional `auth`/`resolved_values`
  parameters, defaulting to `None` so every existing caller's behaviour
  is unchanged (confirmed: the full pre-Phase-4 test set across both
  files still passes unmodified). The `token` method's live round-trip
  (the Plan allowed skipping if infeasible) turned out fully feasible
  without any real git server at all — the generated `GIT_ASKPASS`
  helper script is invoked directly, exactly as git itself would, and
  asserted to print the real resolved token back; a genuine functional
  assertion, not merely a constructed-string check. One platform
  subtlety handled directly rather than assumed: `GIT_ASKPASS` must be
  directly executable and Windows has no shebang support, so the helper
  is generated as `.bat` on Windows and a `chmod 700`'d `#!/bin/sh`
  script elsewhere — verified by actually running the generated script
  on this session's real (Windows) dev environment, not just reasoned
  about. 17 new tests across `test_models_auth.py` (6),
  `test_integrations_git_push.py` (9), and `test_remote_resolution.py`
  (2). Full check suite green (mypy 133 files, ruff, import-linter,
  pytest 1792 passed / 1 known pre-existing unrelated failure). **One
  real, bounded gap found and explicitly flagged, not silently left
  implicit**: `BaseGitOpsIntegration`'s `plan`/`deploy`/`destroy` (Phase
  3) still have no path to a real `AuthenticationModel`+resolved secret
  values at actual deploy time, since `deploy_controller.py`'s generic
  dispatch loop never resolves a remote's `integration` field or passes
  deploy-scoped secrets down that far for *any* tool — unlike Phase 3's
  provisioner/remote gap, this one can't be closed with a build-time
  sidecar (secrets are deploy-time-only, ADR-0022 D4, so persisting one
  to an on-disk, reviewable build artifact would leak it). All 4 phases
  of this Implementation Plan are now done; this last connection
  (`deploy_controller.py` resolving `remote.integration` and threading
  real resolved values into the generic `plan`/`deploy`/`destroy` calls)
  is real, scoped, separable follow-up work, left for a real consumer
  that actually needs a non-ambient GitOps remote to justify building it
  against — matching this whole design's "evidence over assumption"
  discipline one more time rather than guessing the shape unscheduled.
- 2026-10-01: Designed Phase 5 ("design first" — not yet planned into a
  checklist or implemented), to close Phase 4's own flagged gap. Found,
  by reading `deploy_controller.py`'s per-step loop scope directly rather
  than assuming the Phase 3 sidecar pattern was the only option, that
  this is simpler than Phase 4's gap description implied: that loop
  already has `remotes`/`index` in scope at exactly the point `plan`/
  `deploy` are called, so `remote.integration` resolves to a real
  `IntegrationModel.spec.authentication` fresh, at actual deploy time —
  no sidecar, no `build_controller.py`/`gitops.py` `prepare()` change at
  all (an improvement over persisting anything at build time: an
  `Integration` document could change between `build run`/`deploy run`,
  and resolving auth fresh is also the more honest reflection of
  "secrets are deploy-time-only", ADR-0022 D4 — unlike Phase 3's
  `remote`/`output_file`, nothing about auth resolution belongs at build
  time to begin with). Full design: `deploy_controller.py`'s per-step
  loop gains the same `index.get(PlatformKind.INTEGRATION, ...)` lookup
  `integration_resolution.resolve_integration()` already uses for
  `provisioner.integration`, applied to `remote.integration` instead,
  threading `auth=<AuthenticationModel or None>, resolved_values=
  resolved.values` onto the existing `plan()`/`deploy()` calls (absorbed
  harmlessly by every other integration's own `**kwargs: Any`, same
  reasoning already proven for Phase 3's `remotes`/`root`) and onto
  `collect_step_outputs()`'s own `output()` call (its existing `except
  TypeError: return {}` already tolerates a GitOps-unaware integration
  not accepting the new kwargs, with zero new fallback logic needed).
  `gitops.py` itself needs **no new credential logic at all** — `plan()`/
  `deploy()`/`destroy()`/`output()` just forward `auth`/`resolved_values`
  straight through to `ensure_checkout()`/`push_file()`/`remove_file()`,
  which Phase 4 already built to do the real work. Explicitly noted as
  out of scope: there is still no `deploy destroy`/`strata destroy run`
  command wired to call `InfraIntegration.destroy()` for *any* tool
  (pre-existing gap, not created or worsened here) — `destroy()`'s
  credential wiring reaches it identically whenever that command is
  eventually built.
- 2026-10-02: Reviewed the Phase 5 design directly against the real code
  (per request — "review the gap design") before any implementation.
  Found one real bug in the original framing and fixed it in place: the
  plan assumed every `plan`/`deploy`/`output` method tolerates extra
  kwargs via `**kwargs: Any`, citing Phase 3's `remotes`/`root` precedent
  — true for `plan()`/`deploy()` on every reachable integration, but
  checked each real `output()` implementation directly and found
  `TerraformIntegration.output()` has **no** `**kwargs: Any`, unlike its
  own sibling `plan`/`deploy` — and it's the one real, currently-reachable
  consumer of `collect_step_outputs()`'s generic call (Helm has no
  `output()`; Compose's is `Capability.CONTAINER`, so the loop's container
  branch already `continue`s first, confirmed by reading the loop
  directly). Unconditionally passing `resolved_values=resolved.values`
  (always a real, non-empty dict) through for *every* step, not just
  GitOps ones, would have made every Terraform step's `output()` call
  raise `TypeError` — silently caught by the existing `except TypeError:
  return {}`, permanently degrading every real Terraform deployment's
  `${output:...}` cross-step token resolution to empty. A silent
  regression, not a crash — exactly the failure class this design
  discipline exists to catch before it ships, not after. Fixed in the
  design: `auth`/`resolved_values` must be computed conditionally (`None`
  for any non-GitOps step) and `collect_step_outputs()` must only add
  them to its `output(...)` call when at least one is non-`None`, so
  Terraform's own call stays textually unchanged; also add `**kwargs:
  Any` to `TerraformIntegration.output()` itself as independent
  defense-in-depth, matching its own `plan`/`deploy` siblings. Also
  corrected two smaller framing inaccuracies: (1) `remote.integration`
  has no `References(...)` validation (confirmed directly, unlike
  `provisioner.integration`, which does) — a missing/typo'd name must
  silently resolve `auth = None`, not raise `UsageError` the way
  `resolve_integration()` does for the field that *is* validated; (2)
  `gitops.py`'s `output()` doesn't "already read `auth`/`resolved_values`
  out of existing kwargs" — it currently does `del json_format, kwargs`,
  discarding everything, so it needs real new kwonly params, not merely
  a read of something already there. Still not planned into a detailed
  checklist or implemented — this was a design review only.
- 2026-10-02: Implemented Phase 5, exactly per the reviewed/corrected
  design. `TerraformIntegration.output()` gained `**kwargs: Any` (defense-
  in-depth). `deploy_controller.py`'s per-step loop resolves `remote.
  integration` -> `IntegrationModel.spec.authentication` via the same
  `index.get(PlatformKind.INTEGRATION, ...)` pattern `resolve_integration()`
  uses — but, correctly, without its `UsageError`-on-missing behaviour,
  since `remote.integration` has no `References(...)` validation backing
  it — only when `provisioner.gitops is not None`; `auth`/
  `step_resolved_values` stay `None` for every other step, and that `None`
  pair is threaded through `plan()`/`deploy()`/`collect_step_outputs()`
  unchanged. `collect_step_outputs()` itself only adds `auth`/
  `resolved_values` to its `output(...)` call when at least one is
  non-`None`, keeping every non-GitOps tool's call textually identical to
  before this phase — confirmed by the full pre-existing
  `test_deploy_controller.py` suite passing unmodified. `gitops.py`'s
  `plan()`/`deploy()`/`destroy()` pull `auth`/`resolved_values` out of
  their existing `**kwargs` (one line each); `output()` gained real new
  kwonly params (it previously discarded `**kwargs` via `del`) — all four
  forward unchanged into `ensure_checkout()`/`push_file()`/`remove_file()`,
  zero new credential logic in that file. 15 new tests: 5 in
  `test_deploy_controller.py` (the conditional `collect_step_outputs()`
  behaviour for both a non-GitOps and a GitOps-shaped call, plus a direct
  regression test against the real `TerraformIntegration.output()` that
  would have failed before the `**kwargs` fix), 4 in
  `test_integrations_gitops.py` (`plan`/`deploy`/`destroy`/`output` each
  verified, via monkeypatching, to forward `auth`/`resolved_values` to
  their respective `git_push.py` primitive unchanged), 1 in
  `test_integrations_terraform.py` (the exact regression scenario the
  review found, asserted directly). Full check suite green: mypy (133
  files), ruff, import-linter (1 kept, 0 broken), pytest (1801 passed, 1
  known pre-existing unrelated failure). All 5 Implementation Plan phases
  are now complete — `tool: argocd`/`flux` works end to end today, ambient
  or with real `ssh_key`/`token` credentials resolved fresh at actual
  deploy time, never persisted to disk.
- 2026-10-02: Full code review of the implementation (all 5 phases), per
  direct request. Found and fixed 4 real gaps, from critical to low:
  - **(Critical, OWASP A01 path traversal)** `ProvisionerGitOpsModel.
    output_file` had **zero** path-traversal validation, despite
    `git_push.py` joining it verbatim onto the checkout directory
    (`checkout_path / output_file`) and writing there directly, before
    `git add` ever runs. `pathlib`'s `/` silently *discards* the left
    side entirely when the right side is absolute — an unvalidated
    `output_file` of `/etc/cron.d/evil` or `../../../../home/x/.ssh/
    authorized_keys` would write exactly there. The exact same risk
    class `SourceModel.source_path`/`.target_path` already guard via
    `strata.utils.path_safety.validate_relative_path()` — added the
    identical `@field_validator` to `output_file`; no reason it was ever
    the one exception. 4 new parametrized tests.
  - **(Real gap)** `SSHKeyAuthenticationModel.passphrase` was modelled
    (schema validates, documents real intent) but never actually
    consulted by `prepare_git_credentials()` — an encrypted private key
    had no non-interactive way to supply it (would hang on a prompt or
    fail). Fixed: when `passphrase` is set, generates an `SSH_ASKPASS`
    helper (reusing the existing askpass-script machinery) and sets
    `SSH_ASKPASS_REQUIRE=force` (OpenSSH 8.4+, makes ssh use it
    unconditionally rather than only when it guesses there's no
    terminal). 3 new tests, including a real invocation of the generated
    script confirming it prints the actual passphrase back.
  - **(Real gap)** `TokenAuthenticationModel.username` was modelled
    ("Literal username to pair with the token... e.g. 'x-access-token'
    for GitHub") but never consulted — the askpass script answered
    *every* git prompt, including the initial "Username for..." one,
    with the raw token value. Worked by accident for providers lenient
    about username content, but not what the field's own docstring
    promises. Fixed: the askpass script now branches on its own prompt
    argument (`*sername*` -> the configured username, falling back to
    the token if unset; anything else -> the token) — POSIX via a `case`
    statement with `${VAR:-default}` fallback, Windows via a small
    `.ps1` (see next bullet) with an equivalent `-match` branch. 2 new
    tests covering both the username-configured and username-unset
    cases, each invoking the real generated script with both prompt
    shapes.
  - **(Hardening, bundled with the two askpass fixes above)** The
    Windows askpass script was a raw `.bat` using `@echo %VAR%` —
    batch's command-line parsing considers `&`/`|`/`<`/`>`/`^` in the
    *substituted* value, not just literal script text, so a credential
    value containing one of those could have altered the line's
    structure instead of being echoed verbatim. Rewritten as a small
    `.ps1` (reads `$env:VAR` directly, no `cmd.exe` metacharacter
    parsing of the value at all) wrapped by a thin `.bat` launcher
    (`GIT_ASKPASS`/`SSH_ASKPASS` must be a directly executable path, not
    a command line, so the launcher is the actual value those env vars
    point at). The POSIX script was already safe (double-quoted `$VAR`
    expansion never re-parses the value) and is unchanged.
  - **(Lower severity, broader scope)** `SolutionRemoteModel.reference`
    is used as a directory-path segment by three different
    checkout-path functions (`layout.remote_checkout_path()`,
    `.audit_push_checkout_path()`, `.gitops_push_checkout_path()`) but,
    unlike `remote.name` (constrained `PlatformName` pattern, safe by
    construction), had no path-traversal validation at all — fixed with
    a new `@field_validator` on `SolutionRemoteModel.reference` itself
    (benefits all three consumers, not just GitOps) using
    `validate_no_path_traversal()` rather than the normalizing
    `validate_relative_path()`, since a real git ref legitimately
    contains internal `/` (`feature/foo`) that must not be rewritten. 3
    new tests, including one confirming a real branch-name-with-slash
    still validates.
  - **Explicitly left as accepted, documented limitations, not "fixed"**:
    (a) no locking around the shared `gitops-push/<remote>/<branch>`
    checkout directory for concurrent `deploy run`s — inherited from
    `audit_push.py`'s own pre-existing pattern, not a new regression,
    and out of this review's scope to redesign; (b) a temp credential
    file (private key, passphrase/token askpass script) left on disk if
    the process is hard-killed between creation and cleanup — inherent
    to the chosen approach, the same tradeoff most CLI tools handling
    ephemeral secrets make.
  - 12 new tests total across `test_models_provisioning.py` (4),
    `test_models_solution.py` (3), `test_integrations_git_push.py` (5).
    Full check suite green: mypy (133 files), ruff, import-linter (1
    kept, 0 broken), pytest (1814 passed, 1 known pre-existing unrelated
    failure, every fix's regression test passing on this session's real
    Windows dev environment, not just reasoned about).
- 2026-10-02: Follow-up review ("any remaining gaps?") found and fixed 3
  more real issues, all stemming from the previous pass's own fixes:
  - **(Self-introduced regression, fixed)** The `passphrase` fix itself
    broke `prepare_git_credentials()`'s own "every raise happens before
    any filesystem side effect" invariant — the passphrase-reference
    check ran *after* `tempfile.mkdtemp()`/writing the private key, so a
    misconfigured passphrase reference would raise `IntegrationError`
    with no `cleanup` callback ever returned to the caller, leaking that
    tempdir (private key included) permanently. Fixed by resolving and
    validating the passphrase *before* touching the filesystem, matching
    the private-key check's own existing order. New regression test
    asserts no new `strata-gitops-ssh-*` tempdir survives a raise.
  - **(Real gap, found this pass)** `IntegrationError` (from
    `prepare_git_credentials()`, via `credential_env()`) could escape
    `BaseGitOpsIntegration.plan()`/`.deploy()`/`.destroy()`/`.output()`
    completely uncaught — it's a plain `Exception`, not a `StrataError`,
    so a misconfigured GitOps remote's credentials (e.g. a typo'd key
    reference) would crash `deploy_run()` with a raw Python traceback
    instead of a clean diagnostic. Every *other* failure mode in these
    four methods already returns a failed `CommandResult` rather than
    raising — `build_controller.py`'s own `prepare()` call site already
    had to learn this identical lesson for `IntegrationError` specifically
    (its own comment: "would otherwise escape `command_run()`'s `except
    StrataError` as a raw traceback"). Fixed by wrapping each method's
    `ensure_checkout()`/`push_file()`/`remove_file()` call in `try: ...
    except IntegrationError as exc: return CommandResult(returncode=1,
    stdout="", stderr=str(exc))` — restores the "this class never
    raises" contract uniformly. 4 new tests, one per method.
  - **(Same risk class, latent but fixed for consistency)**
    `remote_resolution._git_clone()` has the identical
    `credential_env()`-based path (`auth`/`resolved_values`, Phase 4) —
    not yet reachable from any real caller today (no production code
    passes non-`None` `auth` to `resolve_remote()` yet), but fixed now
    rather than left as a future landmine: wrapped in `try: ... except
    IntegrationError as exc: raise RemoteResolutionError(...) from exc`,
    converting to this module's own already-established error type
    (matching how every other failure in this function already raises
    `RemoteResolutionError`, never a raw `IntegrationError`). 1 new test.
  - 6 new tests total across `test_integrations_git_push.py` (1),
    `test_integrations_gitops.py` (4), `test_remote_resolution.py` (1).
    Full check suite green: mypy (133 files), ruff, import-linter (1
    kept, 0 broken), pytest (1820 passed, 1 known pre-existing unrelated
    failure).
