# Module Model — v2 Design Decisions

- Status: partially-implemented — model and thin service ported; v1's
  service-layer "Phase 1.5" check folded into a model validator (see below)
- Date: 2026-09-21
- Revised: 2026-09-21 — found and fixed a real path-traversal gap in
  `ModuleFileModel.source`/`target` during review (present in v1 too, not a
  new regression). See Decision 7 below.
- Revised: 2026-09-21 — `ServiceDeployerType` replaced by the unified
  `ProvisionerType` + `WORKLOAD_DEPLOYER_TYPES` (common_models.py), decided
  while designing `Provisioner`/`ProvisioningStep`. See
  [ADR-0011](0011-topology-and-provisioning-decoupling.md) and Decision 8
  below.
- Related: [ADR-0002](0002-requirement-interface-injection-grant-lessons-from-v1.md)
  (Requirement rejection, unified Value token syntax — both applied here),
  [ADR-0005](0005-dns-model-design-decisions.md)/[ADR-0007](0007-network-model-design-decisions.md)/
  [ADR-0008](0008-firewall-model-design-decisions.md) (same patterns applied
  to DNS/Network/Firewall first)

## Context and Problem Statement

`ModuleModel` (`src/strata/models/module_model.py`,
`src/strata/services/module_service.py`) is the sixth v2 kind — the "lowest
level application model" (a deployable workload: one or more containers/
sub-charts, their env vars, mounts, health checks, and source location).
It's also the first kind ported so far with genuine build/deploy-layer
entanglement (container images, Helm/Compose specifics, cross-module
dependencies) rather than being purely declarative infrastructure.

## Decisions

### 1. `SourceModel`/`ServiceDeployerType` promoted to `common_models.py`

Ported verbatim from v1 (both already lived in v1's `common_models.py`, not
`module_model.py`) — `SourceModel`'s git-based-vs-chart-based mutually
exclusive union, and its path-traversal/absolute-path guards
(`validate_relative_path`), are genuinely reusable and security-relevant, not
module-specific. No behavior changes from v1.

### 2. `spec.references`/`ModuleReferenceModel` not ported

Same removal as every other kind (ADR-0002). v1's service-layer check that
re-validated `environment[].var/secret/feature` against `spec.references`
(inside `ModuleService._validate_self()`) is dropped along with it — it only
ever re-validated internal consistency, never anything external.

### 3. `ModuleServiceEnvironmentModel` unified into one `value: str` field

v1's 4-way `value`/`var`/`secret`/`feature` union (with its own hand-rolled
"exactly one of" validator) is replaced by the same unified Value-token
approach used for DNS/Network/Firewall: a single required `value: str` field
that's a literal or contains `${var:KEY}`/`${secret:KEY}`/`${feature:KEY}`
tokens. This is the fourth kind confirming the token mechanism generalizes
cleanly — Module was in fact one of the *original* v1 kinds ADR-0002 cited
(`ModuleServiceEnvironmentModel(ValueSourceModel)` in the superseded proposal)
as evidence the old per-kind discriminated-union pattern needed replacing.

### 4. `services[].depends_on` validation moved from service layer to model validator

v1's `ModuleService._validate_self()` ("Phase 1.5") validated two things:
intra-module `depends_on` entries exist as real service names, and
`environment[].var/secret/feature` refs are declared in `spec.references`.
The second check is gone with `references` (#2 above). The first — genuinely
useful, structural, self-contained — is ported as
`ModuleSpecModel.validate_depends_on()`, a Pydantic `model_validator`, since
it only needs the document itself. No separate service-layer "Phase 1.5"
step exists in v2; `ModuleService` has no logic beyond `_get_model_class()`.
Cross-module `@module/service` entries are still syntax-checked only
(unresolved names require build-time context that doesn't exist yet).

### 5. Everything else ported structurally unchanged

`ModuleFileModel` (glob/target validation), `ModuleEndpointModel`,
`ModuleCheckModel`, `ModuleMountModel` (volume_ref/storage_class mutual
exclusion + storage_size requirement), `ModulePropertiesModel`,
`ModuleSpecModel`'s `compose_file`/`services` mutual exclusion and unique
service names — all ported as-is. No Value-token support added to these
(e.g. `port: int`, `storage_size: str`) since v1 never needed it there and
no real gap was cited (same discipline as Firewall's `port`, ADR-0008).

### 6. `PlatformKind.MODULE` added

`MODULE = "module"` added to `PlatformKind` (seventh member).

### 7. Found and fixed a real path-traversal gap: `ModuleFileModel.source`/`target`

Review finding (2026-09-21): `SourceModel.source_path`/`target_path` reject
absolute paths and `..` (`validate_relative_path`, security-relevant, ported
in Decision 1) — but `ModuleFileModel.source`/`target` (`spec.files[]`,
copying files verbatim into the module's build output directory) had **no**
such check, in v1 or in the initial v2 port. Since `target` is joined onto a
build output directory, an unchecked `target: "../../../etc/traefik.yaml"`
(or a malicious `@repo/`-sourced module declaring such a target) could write
outside the intended build directory — a real path-traversal risk (OWASP
A01/A03-adjacent), not hypothetical.

Fixed by extracting `SourceModel`'s validation into a standalone
`validate_no_path_traversal()` (common_models.py, raise-only, no
normalization) alongside the existing `validate_relative_path()` (validates
*and* normalizes — strips leading/trailing slashes). `ModuleFileModel` uses
the raise-only variant, since a trailing `/` on `target` is semantically
meaningful there (marks a directory target for glob sources) and must not be
stripped — using the normalizing variant would have silently broken
`validate_glob_requires_dir_target()`. For `@repo/...` cross-repo sources,
only the part after the `@reponame/` segment is checked (the segment itself
isn't a filesystem path); `@infra/../../../etc/passwd` is still rejected.

Further deduplicated when reviewing `NamespaceModel` (ADR-0010): the
`@reponame/` stripping logic was initially duplicated between
`ModuleFileModel` and `NamespaceModuleModel`; extracted into one shared
`validate_file_ref_no_traversal()` (common_models.py) both now call.

### 8. `ServiceDeployerType` replaced by `ProvisionerType` + `WORKLOAD_DEPLOYER_TYPES` (2026-09-21)

While designing `Provisioner` (ADR-0011), found that `ServiceDeployerType`
(helm/compose/argocd/script — `ModuleSpecModel.type`) was an exact subset of
v1's separate `ProvisionerType` (terraform/ansible/bicep/script/helm/compose/
argocd/flux — workspace/topology-level "what tool provisions infra"), never
reconciled in v1. Two enums for the same underlying "what tool does the
work" vocabulary, differing only by which subset each context happened to
need, is the same duplication pattern already caught and fixed for
`ModuleReferenceModel`/`NamespaceModuleModel` (ADR-0010/0011).

Resolution: one canonical `ProvisionerType` enum (8 members), reused by both
`Module.spec.type` and the future `Provisioner.tool`. Subsets are expressed
as named, documented `frozenset` constants instead of separate enum classes:
`SYNC_PROVISIONER_TYPES` (argocd/flux — no source dir needed) and
`WORKLOAD_DEPLOYER_TYPES` (helm/compose/argocd/script — valid for
`Module.spec.type`, replacing `ServiceDeployerType`'s old vocabulary
exactly). `ModuleSpecModel` gained a `validate_type_is_workload_deployer()`
field validator rejecting terraform/ansible/bicep with a clear message,
since those manage infrastructure state, not container workloads.

Deliberately **not** built: a hardcoded "integration-aware" subset
(terraform/ansible/bicep, per v1's `WorkspaceIacModel.integration` gating).
That's a capability the not-yet-built `Integration` kind should declare
itself once it exists (same reasoning as Provider/Resource type-checking
against `Configuration`'s registry, not a Python constant) — not something to
pre-bake into `common_models.py` now.

### 9. `Module.spec.type` is an open string, not a closed `ProvisionerType` enum (2026-09-21)

Decision 8 (above) initially typed `type: ProvisionerType`. Revisited
immediately after checking v1's real extensibility: `strata/deployers/factory.py`'s
`DeployerFactory` is a genuine plugin architecture — built-in tools
(`_BUILTIN_MAP`, the 8 `ProvisionerType` values) **and** user-registered
plugins discovered from `.strata/provisioners/*.py` (optionally with a
`provisioner.yaml` manifest, `ProvisionerManifestModel`), tracked together
in a runtime registry (`DeployerFactory.is_known_type()`). A closed Python
`Enum` would make a legitimately-supported custom provisioner name fail
schema validation — contradicting a real, working v1 feature, not a
hypothetical one.

Fixed: `type` is `PlatformName | None` (an open string), matching how
`Provider.type`/`Topology.type` are already open strings checked against a
registry rather than closed enums. `ProvisionerType` remains useful as the
set of *known* built-ins for classification (`WORKLOAD_DEPLOYER_TYPES`).
`validate_type_is_workload_deployer()` now: if `v` matches a recognized
built-in that's NOT a workload deployer (terraform/ansible/bicep), reject it
— that's a real, known mismatch; if `v` matches no recognized built-in,
allow it through unchecked — it may be a custom plugin, and confirming that
requires the plugin registry (deferred to Phase 2, mirroring
`DeployerFactory.is_known_type()`, not built in v2 yet). Same "unknown →
defer, don't reject" philosophy as Configuration's `additional_topologies`/
`additional_resources` escape hatches.

## Consequences

- Good: `SourceModel`'s security-relevant path validation is preserved
  exactly, now available to any future kind that needs a source reference.
- Good: fourth confirmation that the Value-token mechanism generalizes
  (DNS → Network → Firewall → Module), including the case ADR-0002 originally
  cited as motivating evidence.
- Good: `depends_on` validation is simpler in v2 — one Pydantic validator
  instead of a separate service-layer "Phase 1.5" pass — with identical
  behavior.
- Good: one shared `ProvisionerType` vocabulary instead of two independently-
  drifting enums, caught before `Provisioner` was ever built with a third.
- Neutral: Module is the most build/deploy-layer-entangled kind ported so
  far; several fields (`type`, `configuration`, `release_name`,
  `kubernetes_namespace`) remain inert until a provisioner/build layer
  exists to consume them, same as Provider's `configuration`/`custom` were
  before any builder existed (ADR-0003).
- Good: a real path-traversal gap (Decision 7) was found and fixed rather
  than silently ported forward — `ModuleFileModel` now has the same
  protection `SourceModel` already had.

## Remaining Work

- Environment cross-check is tracked centrally in
  [docs/design/value-token-resolution.md](../design/value-token-resolution.md),
  not duplicated here.
- Cross-module `@module/service` dependency resolution requires the
  workspace/namespace layer (which modules are grouped under which
  namespace) — not built in v2 yet.
