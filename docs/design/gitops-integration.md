# GitOps Integration (ArgoCD / Flux) — Design

- Status: current — all 5 implementation phases complete and code-reviewed.
  `tool: argocd`/`flux` provisioners work end to end today: schema
  (`ProvisionerGitOpsModel`), rendering (reuses `output.template`), the git
  commit/push primitive (`git_push.py`), the `ArgoCDIntegration`/
  `FluxIntegration` classes, and real `ssh_key`/`token` credentials resolved
  fresh at deploy time. Two code-review passes (2026-10-02) found and fixed
  a path-traversal gap, a tempdir leak, and an uncaught `IntegrationError`
  — see [History](#history).
- Last updated: 2026-10-06 (graduated from `docs/work/`)

## Overview

v2's schema already fully anticipates ArgoCD/Flux as `ProvisionerType`
members (`SYNC_PROVISIONER_TYPES = {ARGOCD, FLUX}`,
`strata/utils/builtin_types.py`) — a sync/GitOps-style provisioner needs no
`source`, since it "renders from the platform artifact instead." Both
`build_controller.py`'s `build_run()` and `deploy_controller.py`'s
`deploy_run()` special-case a source-less provisioner's path computation
correctly (source-less/sync-GitOps provisioners key off `step.name`,
matching build's own other branch).

**The real mechanism is much simpler than "talk to Kubernetes/ArgoCD/Flux"**
— confirmed by reading v1's actual implementation directly
(`deployers/sync_deployer.py`, `BaseSyncDeployer`, shared by both ArgoCD and
Flux): **it's a git commit-and-push, nothing more.** The in-cluster GitOps
controller (already running, entirely outside strata's scope) watches a git
repo on its own poll/webhook cycle; strata's only job is to render a config
file and push it there. No Kubernetes API, no ArgoCD/Flux API call anywhere
in the actual deploy path — those controllers' own API is only ever queried
for a read-only status check (`health`), never to trigger anything.

## v1 Background — evidence, read directly from `BaseSyncDeployer`

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
repo to push into). `SyncBuilder` (the build-time half) is what actually
produces the rendered file `sync_deployer.py` later commits — the key fact
is the render itself is **not** GitOps-specific machinery.

## Current Design (v2)

### Schema — one new typed field, same shape as `backend`/`properties`

`ProvisionerModel` already has the precedent for "a field only meaningful
for one tool" (`backend` for terraform, `properties` for ansible, each
validated against `tool` by a `model_validator`). A GitOps provisioner gets
the same treatment — not v1's loose `stage.backend.integration`/`.remote`
(reusing a field already earmarked for terraform state config would be a
type lie, the exact anti-pattern `RemoteFetch`'s own docstring flags
elsewhere in this schema):

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
gated by `validate_gitops_only_for_sync_tools()`, mirroring
`validate_properties_only_for_ansible()`'s "reject only a recognized
mismatch, allow an unrecognized/custom tool through" leniency.

**Worked example** — an ArgoCD provisioner pushing a rendered Application
values file into an already-declared `gitops-config` remote, with no
`source` (sync-type, same as a `terraform`/`helm` provisioner would need
one):

```yaml
# strata.yaml
spec:
  remotes:
    - name: gitops-config
      type: git
      url: https://github.com/acme/gitops-config.git
      reference: main
      fetch: strata
      integration: github-deploy-key   # credentials — see "Authentication" below

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
supply only the *destination*.

### Rendering — a plain values/config file, never the Application/HelmRelease CR itself

`output.template` (`OutputModel`) + `InfraIntegration.
render_output_template()` already exist and are tool-agnostic by design
(ADR-0023 D3: "nothing about this field is tool-specific").

Confirmed against v1's real `builders/sync_builder.py`: v1's `SyncBuilder`
renders **one plain Jinja2 template** from the full platform context
(secrets excluded) to `properties.output_file`, with zero ArgoCD/Flux-
specific knowledge of what that file *means*.

The file this renders is **just the updated values/config file — never the
ArgoCD `Application` / Flux `HelmRelease`/`Kustomization` CR itself.** That
CR is a one-time bootstrap resource (created once, out of band, pointing at
a fixed path in the config repo — e.g. `valueFiles: [apps/forge/values.yaml]`);
the GitOps controller re-reads whatever that path resolves to on every poll.
Re-rendering and re-pushing the CR itself on every deploy would mean strata
re-asserting ownership of a resource ArgoCD/Flux itself usually owns the
lifecycle of (and risks fighting its self-heal) — the same "already exists,
don't regenerate it" category that keeps `platform.json`/`PlatformBuilder`'s
snapshot out of v2's scope. v1's own real config confirms this shape too:
`properties.output_file` is always a single values/config file path, never
an Application-manifest path. The bootstrap CR (if/when needed) is a
separate, out-of-band concern — likely a `strata sln init`-style one-time
scaffold if it's ever built, not part of `build run`/`deploy run`'s
per-deploy loop.

So `build_run()`'s already-resolved `graph` + `output.template` is already
sufficient — no new rendering capability, no ArgoCD/Flux-specific template
logic belongs in strata at all. `default_output()` raises `IntegrationError`
when `output.template` is unset (no sensible default projection exists for
a GitOps push) — the honest-default rule the rest of this layer follows.

### Authentication — fetch (read) and push (write), one capability, two directions

Both `strata.yaml spec.remotes` (fetch — used by every provisioner/module
`source:`) and this design's new GitOps `remote:` (push) are the same
underlying primitive — a `SolutionRemoteModel` naming a git URL + an
optional `integration` for credentials — so they're designed as one
capability with two directions, not two separate auth stories.

**Neither direction authenticates through strata by default.**
`remote_resolution._git_clone()`'s own docstring: "No credential handling
yet... relies entirely on whatever git already has configured"; the same is
true for push by construction. Both are "ambient only" — whatever SSH
agent/credential helper/`~/.gitconfig` the process already has — which has
quietly been sufficient so far because every real fetch remote checked
(haven, config-deploy) is either public or already reachable via the
operator's/CI runner's own pre-configured git auth. **Push is different in
kind**: a write to a remote repo essentially never works anonymously, so a
real GitOps push is the first case that forces the question to actually be
answered.

**The capability shape: one `SourceIntegration`, both directions.**
`Capability.SOURCES` already exists in `integration_model.py` (declarable,
no ABC yet — ADR-0021 D9 names it as a real, scheduled future capability).
The natural design is **one** `SourceIntegration` class serving both
`fetch`/`checkout` (today's `resolve_remote()`/`_git_clone()`, which would
move under it) and `push` (this design) — same transport (`run_command()`
wrapping the real `git` CLI), same credential setup step, differing only in
the git subcommand run at the end. Splitting fetch and push into two
different credential mechanisms would model an implementation accident
(which direction happens to need write access) as if it were a capability
difference, when it isn't one. **Not built yet** — `push_file()` and the
credential helpers below work standalone and don't block on it.

**Ambient identity is enough only for the same-repo case, not the
realistic cross-repo GitOps one.**

- **Same-repo push** (the GitOps config lives in the same repo the
  pipeline already checked out): plausible the ambient identity already
  has write access — v2's own real CI confirms this (`ci-release.yml`/
  `ci-docs.yml` push using nothing but GitHub Actions' auto-issued
  `secrets.GITHUB_TOKEN`).
- **Cross-repo push** (the GitOps config lives in a separate, shared repo
  — the far more common real GitOps topology): the ambient identity
  essentially never has access. This is a hard platform boundary, not a
  strata gap: GitHub Actions' auto-`GITHUB_TOKEN` is cryptographically
  scoped to only the triggering repository by GitHub itself; an Azure
  DevOps pipeline's SPN *can* be granted cross-repo access, but only if
  someone deliberately provisioned that grant.
- **The fallback-chain pattern to reuse already exists in this codebase**
  — `AzureKeyVaultResolver` wraps `azure.identity.DefaultAzureCredential`
  (managed identity / workload identity / environment / `az login`, in
  that order, zero strata-specific configuration for the common case).
  `SourceIntegration` should follow the identical shape: default to
  ambient git config, fall back to an explicit credential only when the
  remote being pushed to isn't one the ambient identity already covers.

**`AuthenticationModel` had no git-native method, in either v1 or v2.**
v2's closed `method` vocabulary (`oauth2`/`aws`/`gcp`/`api_key`/
`certificate`/`saml`/`cli`/`managed_identity`) is an exact match for v1's
own — inherited, not a v2-introduced gap. None of these cleanly models an
SSH deploy key or an HTTPS PAT, the two real-world git credential shapes
(`certificate`'s fields are TLS-flavored; `api_key`'s `header_name` assumes
HTTP-header delivery, not how git consumes a PAT). Two new methods were
added to the existing closed vocabulary instead of a separate git-only auth
model — matching `SolutionRemoteModel`'s own stated philosophy
("Credentials are never inline here — Integration is strata's single
credential mechanism"):

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

Added as `method: Literal[..., "ssh_key", "token"]` +
`ssh_key: SSHKeyAuthenticationModel | None` /
`token: TokenAuthenticationModel | None` fields,
`validate_method_matches_populated_config()`'s `method_fields` tuple
extended to match.

### Credential delivery mechanics — keeping the value out of `argv`

Embedding a token directly in the clone/push URL
(`https://x-access-token:<token>@host/...`) is explicitly **not** safe
enough: it puts the token in `argv`, visible via `ps`/Task Manager to any
user on the same host — the exact leak class `run_command()`'s own
docstring names as the reason secrets go through `input`/`env`, never
`args`. Two real mechanisms instead, both expressible purely through
`run_command()`'s existing `env=` kwarg — no new transport:

- **`method: ssh_key`** — writes the resolved private key to a short-lived
  `0600` temp file, sets `GIT_SSH_COMMAND="ssh -i <path> -o
  UserKnownHostsFile=<known_hosts path, if supplied>"` in `env=` (else
  `StrictHostKeyChecking=accept-new` so a non-interactive run never hangs
  on a prompt), and deletes the temp file in a `finally` once the git
  subprocess returns.
- **`method: token`** — sets `GIT_ASKPASS` (in `env=`) to a generated
  helper script (`.bat` on Windows, `#!/bin/sh` + `chmod 700` otherwise —
  `GIT_ASKPASS` must be directly executable, and Windows has no shebang
  support) that echoes a token read from *another* env var (also set in
  `env=`, never in `args`) — git itself invokes `GIT_ASKPASS` to obtain
  the password, so the token is never embedded in the URL or any
  command-line argument.

Both live in one shared function, `prepare_git_credentials()`
(`strata/integrations/git_push.py`), wired into
`push_file()`/`remove_file()`/`ensure_checkout()` via a `credential_env()`
context manager (guarantees cleanup always runs) and into
`remote_resolution._git_clone()`/`resolve_remote()` — confirming fetch and
push are one capability, not two. Takes an already-resolved
`resolved_values: Mapping[str, str]` alongside the `AuthenticationModel` —
it only does the key-reference-name -> env-shape translation; resolving a
key reference to its real secret value stays a controller-layer concern
(`integrations/` must not reach for it — ADR-0003 layering).

### The push primitive — `push_file()`/`remove_file()`

`strata.controllers.remote_resolution.resolve_remote()` only ever
clones/checks out a remote (pull direction); there was no push capability
anywhere in v2. `strata/integrations/git_push.py` adds it — pushing is an
`Integration`-capability concern, not a `controllers`-layer "resolve a
path" concern:

```python
def push_file(
    remote: SolutionRemoteModel, root: Path, rendered_file: Path, output_file: str, message: str, *,
    auth: AuthenticationModel | None = None, resolved_values: Mapping[str, str] | None = None,
) -> PushResult:
    """Ensure a fresh checkout of `remote`'s branch, copy rendered_file to
    output_file inside it, git add/commit/push. Returns a PushResult
    (success, detail) — never raises; the caller decides whether a failed
    push fails the deploy."""
```

Mirrors `controllers/audit_push.py`'s own proven clone/fetch/reset/add/
commit/push sequence and its "never trust an existing checkout" shape
(same `git push origin HEAD:<branch>` refspec trick, surviving a
detached-HEAD CI checkout; same empty-remote-repo fallback to
`origin/HEAD`; same local, not global, git identity via `resolve_actor()`)
— not reused directly (`integrations/` may not import `controllers/`,
ADR-0003), but deliberately mirrored rather than re-derived. Its own
checkout directory (`layout.gitops_push_checkout_path(root, remote,
branch)`) is kept separate from `remote_checkout_path()`'s read checkouts
and `audit_push.py`'s own audit-push checkouts, so none can collide.
`remove_file()` is `destroy()`'s mirror-image primitive; a public
`ensure_checkout()` is `plan()`'s/`output()`'s read-only primitive (a
fresh checkout, no write) — all three share one `_commit_and_push()` tail
and one `_validate_remote()` guard.

### The Integration class(es)

One shared base (`BaseGitOpsIntegration`) implementing `InfraIntegration`
(`plan`/`deploy`/`destroy`/`output`), mirroring v1's `BaseSyncDeployer`
split — `ArgoCDIntegration`/`FluxIntegration` each a thin subclass
differing only in `TYPE`:

- `plan()` -> `git diff --no-index` between the newly-rendered file and
  what's currently in the cloned config repo (no mutation). v1's separate
  `check` step has no distinct v2 equivalent — folded into `plan()` itself
  (a failed diff against an unreachable/not-yet-cloned repo already
  surfaces as a `plan()` failure). Exit codes 0 (identical) and 1
  (different, including "new file") are both normalised to a *successful*
  `CommandResult` — only 2+ is a real failure, since
  `deploy_controller.py`'s generic loop only ever checks `.is_successful`.
- `deploy()` -> `push_file(...)`.
- `destroy()` -> `remove_file(...)`.
- `output()` — re-derives everything from a small on-disk sidecar (below)
  + a fresh `ensure_checkout()`, since `collect_step_outputs()` calls it as
  a wholly separate invocation. Returns `{"commit_sha": {"value": <40-char
  SHA>}, "remote_path": {"value": <output_file>}}` — confirmed against
  `collect_step_outputs()`'s own duck-typing: that function is not
  Terraform-specific despite its docstring suggesting so — it only checks
  that `json.loads(stdout)` is a `dict` whose values are
  `{"value": ...}`-shaped. No changes needed there.
- `health` — **not implemented.** The one step needing an ArgoCD/Flux API
  client; deliberately deferred (no real-consumer shape to ground a design
  against, and it's read-only status reporting, never load-bearing for the
  deploy itself).

`COMMAND = "git"` — a correction found while implementing:
`Integration.is_available()` treats a `None` command as unavailable, so
`deploy_controller.py`'s own preflight would otherwise always reject this
tool; `git` is the real external dependency (no `argocd`/`flux` CLI is
ever invoked — `plan`/`deploy`/`destroy` call `git_push.py` directly, not
`self.run()`).

**Build/deploy process-boundary split.** `deploy_controller.py`'s generic
(non-container) dispatch loop calls `integration.plan(path, out_file=...,
env=env)` / `.deploy(path, plan_file=..., env=env)` with only `path` and
`env` — no way for `plan`/`deploy`/`destroy` to know *which* remote/
output_file to push to without another channel. Fix: `prepare()` (called
during `build run`, which has everything) resolves `provisioner.gitops.
remote` and writes a small `.gitops-push.json` sidecar into the step's
on-disk build directory — which (unlike process memory) survives between
the separate `build run`/`deploy run` CLI invocations; `plan`/`deploy`/
`destroy`/`output` read it back from there alone.

**Authentication is resolved fresh at `deploy run` time, never persisted.**
Unlike `remote`/`output_file` (routing, resolved at build time and carried
via the sidecar), `authentication` (secrets) is resolved directly inside
`deploy_controller.py`'s existing per-step loop — which already has
`remotes`/`index` in scope at exactly the point `plan`/`deploy` are
called — **only when `provisioner.gitops is not None`**: `remotes.get(
provisioner.gitops.remote)`, and if it names an `integration`, look it up
via `index.get(PlatformKind.INTEGRATION, ...)` to get its `spec.
authentication`. A missing/typo'd integration name silently resolves
`auth = None` (ambient fallback), never raises —
`SolutionRemoteModel.integration` has no `References(...)` annotation, so
it's schema-declared but not existence-checked. This is strictly better
than persisting anything at build time: an `Integration` document's
`authentication` could change between `build run` and `deploy run`, and
resolving it fresh is also the honest reflection of "secrets are
deploy-time-only" (ADR-0022 D4).

## Related Decisions

- [ADR-0009](../decisions/0009-module-model-design-decisions.md) — unified
  `ProvisionerType` + `SYNC_PROVISIONER_TYPES`/`WORKLOAD_DEPLOYER_TYPES`,
  the schema groundwork this design builds on without any changes needed
  there.
- [ADR-0021](../decisions/0021-integration-layer.md) D9 — `Capability.
  SOURCES`/`SourceIntegration`, a real, scheduled future capability — this
  design's "Authentication" section sketches it serving both fetch and
  push; not yet built.
- [ADR-0021](../decisions/0021-integration-layer.md) — the `Integration`/
  `InfraIntegration` capability split this design's classes implement.
- [ADR-0023](../decisions/0023-build-output-rendering.md) D3 — `output.
  template`/`default_output()`, reused here unchanged.
- [docs/work/remotes.md](../work/remotes.md) — `resolve_remote()`'s
  current pull-only scope; this design's `push_file()` is the first
  push-direction primitive.
- [docs/work/deploy-command.md](../work/deploy-command.md) — cross-step
  outputs, whose `collect_step_outputs()` this design's `output()` method
  stays compatible with.
- `src/strata/models/auth_models.py` — `AuthenticationModel`'s existing
  closed `method` vocabulary and its "key references, never inline
  secrets" convention, which `SSHKeyAuthenticationModel`/
  `TokenAuthenticationModel` extend rather than replace.
- `src/strata/models/provisioning_model.py` —
  `ProvisionerAnsiblePropertiesModel.ssh_private_key_secret`, the real,
  already-shipped precedent for "a secret-reference field holding an SSH
  private key" this design's `SSHKeyAuthenticationModel.private_key`
  mirrors.

## History

- **Mechanism confirmed from v1's real `sync_deployer.py`**: a GitOps
  deploy is a plain git commit+push to a config repo an external
  ArgoCD/Flux controller already watches — no Kubernetes/ArgoCD/Flux API
  call anywhere in the deploy path itself; their API is only ever used for
  an optional, read-only `health` check, deliberately not built (no real
  consumer, never load-bearing).
- **Rendered file is always a plain values/config file, never the
  Application/HelmRelease/Kustomization CR itself** — confirmed against
  v1's real `sync_builder.py`. That CR is a one-time, out-of-band
  bootstrap resource outside `build run`/`deploy run`'s scope entirely;
  `output.template` + `build_run()`'s already-resolved graph were already
  sufficient, so no new rendering capability was needed.
- **`collect_step_outputs()` needed zero changes** — it's duck-typed
  purely on JSON shape (`{key: {"value": ...}}`), never on the
  integration's type, despite its own docstring/comments suggesting
  otherwise.
- **Neither fetch nor push authenticates through strata by default** —
  both are ambient-git-config only. The chosen long-term shape is one
  `SourceIntegration` capability (ADR-0021 D9, still unbuilt) serving both
  directions identically; until then, `AuthenticationModel` gained two new
  methods (`ssh_key`, `token`) rather than a separate git-only auth model.
  Same-repo push can lean on ambient CI identity (e.g. GitHub Actions'
  auto-`GITHUB_TOKEN`); cross-repo push — the realistic GitOps topology —
  essentially never has ambient access and needs one of the two new
  methods.
- **Secret delivery avoids `argv` entirely** — `ssh_key` writes a
  short-lived `0600` temp keyfile and sets `GIT_SSH_COMMAND`; `token` sets
  `GIT_ASKPASS` to a generated helper script reading the token from its
  own env var — both via `run_command()`'s existing `env=` kwarg, no new
  transport.
- **Build/deploy process-boundary split**: `remote`/`output_file`
  (routing) are resolved at `build run` time and persisted in a small
  on-disk sidecar (`.gitops-push.json`) so `deploy run` — a separate
  process — can read them back; `authentication` (secrets) is
  deliberately *not* persisted and is instead resolved fresh inside
  `deploy_controller.py`'s per-step loop at actual deploy time, matching
  ADR-0022 D4 ("secrets are deploy-time-only").
- **A real regression was caught on review before shipping**:
  unconditionally passing `auth=`/`resolved_values=` into every step's
  `output()` call would have silently broken every Terraform deployment's
  `${output:...}` cross-step token resolution (via `collect_step_outputs()`'s
  own `except TypeError: return {}`, since `TerraformIntegration.output()`
  had no `**kwargs`). Fixed by only passing those arguments for an actual
  GitOps step, plus adding `**kwargs: Any` to `TerraformIntegration.
  output()` for consistency with its siblings.
- **Two further code-review passes (2026-10-02)** found and fixed: a
  path-traversal gap (`ProvisionerGitOpsModel.output_file`/
  `SolutionRemoteModel.reference` both now validated), a tempdir leak in
  the passphrase handling, and an `IntegrationError` able to escape
  `BaseGitOpsIntegration`'s methods/`_git_clone()` completely uncaught
  (crashing with a raw traceback instead of a clean diagnostic) — all
  three converted/fixed at the source.
- **Explicitly out of scope, not a gap**: no `deploy destroy`/`strata
  destroy run` command exists yet for *any* tool (pre-existing, not
  created here); a full `SourceIntegration` capability class (ADR-0021 D9)
  remains unbuilt — `push_file()`/the credential helpers were written to
  work standalone so they don't block on it; and there is no locking
  around a shared GitOps push checkout for concurrent `deploy run`s
  (inherited from `audit_push.py`'s own pre-existing pattern).
- **No real consumer evidence yet** — haven's real `workspace.yaml` only
  declares `terraform`/`helm` provisioners; a v2 of `cfg-int-deployment`
  was reportedly in progress but had no GitOps-related commits/branches as
  of the last check. This design is validated only against local
  bare-git-repo tests, not yet exercised by a real GitOps consumer —
  revisit once one appears.
