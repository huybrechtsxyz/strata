# Solution Manifest (`strata.yaml`) and Name-Based Document Discovery

- Status: partially-implemented
- Date: 2026-09-22
- Related: [ADR-0003](0003-provider-model-design-decisions.md) (the
  "no solution-loading layer exists" gap this ADR finally closes the design
  for), [ADR-0014](0014-provider-topology-config-standalone-kinds.md)
  (introduced the `{name, file}` pointers this ADR replaces),
  [ADR-0011](0011-topology-and-provisioning-decoupling.md) (Topology's
  promotion; its parked Phase 2 validators are unblocked by this work)

## Context and Problem Statement

Two problems that turned out to be the same problem.

**1. There was no committed root.** v1 has a `kind: solution` document —
`.strata/solution.json`, with `meta.name` and `spec.repositories[]` — but
v1's `.gitignore` lists `**/.strata/solution.json`, so it is machine-local
state that never survives a clone. Everything durable therefore had to be
duplicated into a committed `config/remotes.yaml`, and in the real
`cfg-int-deployment` repo the two have already drifted: the same `env-int`
remote is `type: bundled` in `remotes.yaml` and `type: gitops` in
`solution.json`, with its location recorded in both (`deploy_path` vs
`path`). `remotes.yaml`'s own comment concedes the coupling — *"These values
must match the `path` recorded for the same repository in
.strata/solution.json, which is what `strata repo add` wrote."* Two sources
of truth, reconciled by hand.

**2. Every reference carried a redundant, unverifiable `name`.** Ten
reference sites were `{name, file}` pairs (`Configuration.providers`/
`.topologies`, `Workspace.{providers,namespaces,firewalls,dns_zones,networks,
topology,resources}`, `ModuleReferenceModel`). `ConfigurationProviderModel.name`
was documented as *"must match the referenced document's meta.name"* — the
same fact in two places, checkable only after loading, with nothing defining
which wins on disagreement. Meanwhile the five parked Phase 2 validators
(`WorkspaceService.validate_topology_references()`/
`validate_topology_components()`, `ProviderService`/`ResourceService.
validate_against_provider_config()`, plus the deferred subnet check) all
already take **already-loaded models keyed by name** — not paths. They were
written against an identity index before one existed.

Surveying comparable tooling: discovery happens *within* a unit (all `.tf`
in a Terraform root module, all of `templates/` in a chart, Ansible's
`roles/<n>/tasks/main.yml`), while references *across* a unit boundary stay
explicit (Terraform `source`, `Chart.yaml` dependencies, Flux `sourceRef`).
Kubernetes settled the reference question outright: references are identity
(`{kind, name}`), never file paths, with the API server as the index.

The two real reference repos rule out convention-based roots: haven groups
by logical unit (`forge/` holds a firewall *and* a vm *and* namespaces),
cfg's `stacks/customer/` holds workspace + environment + deployment
together. Neither organizes by kind.

## Decision

**1. A committed `strata.yaml` at the solution root (`kind: solution`).**
Found by walking up from the current directory, like `go.mod`/`Cargo.toml`/
`package.json`. It holds solution identity, `spec.configuration` (where
Configuration documents live), and `spec.remotes`. Scope is deliberately
bootstrap-only — *what/where is this solution*, never *what rules govern
it*. Platform policy stays in `Configuration`. Same split as `go.mod`
(module identity and deps) versus build configuration.

**2. No lock-file counterpart.** With `reference` pinned in the manifest and
the checkout location a CLI convention, a lock would mostly re-record
declared facts — which is precisely what produced v1's drift. v1 built only
the lock half of the manifest/lock pattern (`package.json`+`package-lock.json`,
`Chart.yaml`+`Chart.lock`, `go.mod`+`go.sum`); v2 builds only the manifest.

**3. Remotes live on the manifest, not `Configuration`.** Bootstrap ordering
forces it: v1's own `solution.json` registers a `config` repository, i.e.
Configuration itself can live in a remote, so remotes must resolve before
Configuration loads. Configuration holds *policy*; the manifest holds
*composition*.

**4. `type` and `fetch` are separate fields on a remote.** v1 conflated
"what kind of source is this" with "who materialises it", and production
paid for it: `cfg-int-deployment` declares a git repository as
`type: bundled` purely so strata skips its own fetch (Azure Pipelines
already checked it out; strata has no git credentials there). The comment
records the cost — ref pinning and dirty-tree gating both silently stopped
applying. Now stated honestly: `type: git` + `fetch: external`.
`RemoteType` also renames v1's values for accuracy: `GITOPS` → `GIT`
(GitOps is a methodology, not a source type), `CONTAINER` → `OCI` (an OCI
registry serves charts and arbitrary artifacts too), `BUNDLED` → `LOCAL`.

**5. Two v1 remote fields deliberately not ported.** `source_path` made a
remote "a specific thing inside a repo" rather than "a repo", killing reuse
for a second artifact in the same repository. `deploy_path` is CLI
convention, not schema — it is the field that produced the drift in
Problem 1.

**6. Documents are found by discovery and indexed by `(kind, meta.name)`;
`file:` is removed from every internal reference.** Recursive scan from the
solution root, kind taken from each document's own `kind:` field, folder
layout irrelevant — the `kubectl apply -R -f` model. Files with no strata
`apiVersion` are skipped silently (real repos are full of Helm values, CI
YAML and k8s manifests); a strata-versioned file with an unknown `kind` is
an error, because that is a typo. Duplicate `(kind, name)` is a hard error.
Consequently:
- Eight wrapper models are deleted. `Configuration.providers`/`.topologies`
  and `Workspace.{providers,namespaces,firewalls,dns_zones,networks,topology}`
  become `list[PlatformName]`. Their `description` belonged on the target
  document's own `meta` anyway.
- Two pointers are renamed to name a *class*, keeping their instance
  metadata: `WorkspaceResourceModel.file` → `.resource`, and
  `ModuleReferenceModel.file` → `.module`. This makes the class/instance
  split explicit in the schema rather than implied by a path — one Resource
  document can now be instantiated several times under different instance
  names.
- Genuine file paths are untouched: `ScriptPathModel.file`,
  `ModuleFileModel.source`/`target`, `SourceModel.source_path`,
  `ModuleSpecModel.compose_file`.

**7. Discovery boundary is the marker file.** Recursion stops at any
directory containing its own `strata.yaml` — self-maintaining, unlike a
hardcoded `repos/`/`build/`/`.archive/` denylist that breaks the moment
someone clones into `vendor/`. This places the boundary exactly on the repo
edge, matching the "discovery within a unit, explicit across a boundary"
rule the surveyed tools follow.

## Consequences

- Good: one committed declaration of composition — the `remotes.yaml` ↔
  `solution.json` split that already drifted in production cannot recur.
- Good: the redundant `name`/`meta.name` duplication is gone from ten
  reference sites, along with the undefined-precedence question.
- Good: the five parked Phase 2 validators need **no signature changes** —
  they already take an identity-keyed index.
- Good: files can be moved or reorganized without editing any referrer, and
  orphaned documents become detectable.
- Good: root detection from any subdirectory; `--config-path`/`--config-file`
  become overrides rather than requirements.
- Cost — **strata documents must live in the solution repo.** Remotes supply
  *artifacts* (Terraform, charts, copied files), not documents. This ends a
  real v1 capability: `ModuleReferenceModel.file` accepted `@repo/...`, so a
  Module *document* could live in a shared repository. Sized honestly:
  - It is a *repo* constraint, not a *file* one — documents may sit anywhere
    within the solution, which is the point of discovery.
  - Neither reference solution currently relies on it: `iac-int-deployment`
    is pure Terraform, `env-int-deployment` is execution-state JSON.
  - The painful case is **org-wide governance documents** — a platform team
    owning `ProviderConfig`/`TopologyConfig` ("approved regions", "a
    kubernetes topology needs exactly one control-plane role") consumed by
    many solutions. Copying those into every solution repo defeats their
    purpose and guarantees drift.
- Mitigation, deliberately deferred: **remote-qualified identity**
  (`module: "@shared/traefik"`, `providers: ["@platform/azure"]`) — Bazel's
  `@repo//pkg:target` and Nix flake inputs are direct precedent. It keeps
  identity semantics (no paths, no duplicated `name`), stays explicit at the
  boundary, and namespaces the index so two repos may both define `azure`.
  Not built now (nothing consumes it yet — ADR-0003's minimal-slice policy),
  but **the loader's index must be keyed `(remote, kind, name)` with
  `remote=None` for local from day one**, so this is an additive change
  rather than a rewrite. The resolver should accept `@remote/`-qualified
  names and fail them with an explicit "not yet supported" message.
- Cost: `spec.configuration` on the manifest may be redundant. It was added
  arguing Configuration cannot be discovered by rules living inside
  Configuration — but discovery rules live in `strata.yaml`, so Configuration
  is just another discovered document. It earns its place only if config may
  live outside the scan root. Revisit when the loader is built.
- Open: v2 should declare `.strata/` runtime-only. Both real repos keep
  `configuration.yaml`/`logging.yaml` there next to `cache/`, `logs/` and
  `audit.log`, so a blanket exclude would drop real config.
- Cost: provenance becomes mandatory. With `file:` gone, nothing in the
  schema records where a document came from, so the index must carry the
  discovered path and every error must print it — otherwise debugging is
  materially worse than before. This is the main thing `file:` was buying.

## Status of implementation

Implemented: the `solution` kind (`solution_model.py`, `solution_service.py`),
`SolutionRemoteModel` with `RemoteType`/`RemoteFetch`, and the full removal
of `file:` from all ten internal reference sites with their models, services
and tests updated.

Not implemented: the discovery loader / solution controller itself (planned
for `strata.controllers`, the layer v1 places above `services`), remote
materialisation, and the wiring of the five parked Phase 2 validators.
