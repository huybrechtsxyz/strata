# Pinnable artifact references — Design

- Status: implemented — Path 2 (`store: artifact`) done 2026-09-27; Path 1
  (`ModuleServiceModel.artifact` → Compose/Helm rendering) done 2026-10-06
  (see [ADR-0026](../decisions/0026-pinnable-artifact-references.md) and
  "Path 1 Resolution Wiring — Solution Design" below)
- Last updated: 2026-10-06

## Overview

A gap found while validating `build run`/`kind: version` against real
cfg-deployment evidence: **a container image (or chart) reference that
lives on a plain `constant`/`environment` variable has no way to be
version-pinned** — `kind: version`'s `VersionPinsModel` only targets
`ModuleServiceModel.image` (a workload strata itself deploys via
Compose/Helm), `SourceModel.chart_version`, or a `fetch: strata` remote's
`reference`. A variable holding a raw image string is invisible to it.

This doc is for thinking through whether/how strata should model a
reusable, cross-workspace **artifact reference** — something that "can be
many things" (a container image, a chart, maybe other pinned identifiers
later) and is referenced by identity from multiple places, the same way a
`provider`/`tenant`/`module` already is — rather than solving this by
overloading an existing kind or by having `kind: version` reach directly
into a variable's value.

## Real evidence motivating this

- `E:\Sources\cfg-deployment\stacks\ring\environment-dispatcher.yaml`
  declares `dspapi_container_image_name`/`dspapi_container_image_tag` as
  plain `store: constant` variables (tag blank in the base, overridden per
  concrete ring deployment) — no lock, no audit trail, no `reason`/
  `reviewed` tracking, unlike everything `kind: version` already covers.
- **Not an isolated case.** `stacks/ring/environment-datahub.yaml` alone
  declares five more: `datahub_api_container_image_name`/`_tag`,
  `staging_api_container_image_name`/`_tag`,
  `adminapp_container_image_name_with_tag` (combined name+tag in one string
  — that Terraform root's own variable shape), and a commented-out-but-
  recorded `binocs_api_container_image_name`/`_tag` ("known and recorded
  here for when it's turned on"). `stacks/ring/environment-sci.yaml` adds a
  seventh, `sci_container_image_name`/`_tag`, for a separate real product
  estate (SCI). That file's own comment: *"all four images publish under
  the same feed/prefix and share one tag per build"* — i.e. dspapi/
  datahub_api/staging_api/adminapp are meant to be pinned **together, to
  one shared value**. A real, growing registry of named, pinnable image
  references, several of which are meant to share one pin value — not a
  single one-off case.
- These flow into `variables.auto.tfvars.json`, consumed by the
  `dispatcher_api`/`datahub_api` **provisioners** — whole external Terraform
  roots (`source: {repository: iac-int, source_path: "products/dispatcher/terraform"}`).
  The Web App/Function App they manage is never declared as a `resource`/
  `topology` in this solution at all, so there's no attachment point for a
  `Module` (which is only ever reachable via `Namespace.spec.modules` or
  `TopologyComponentModel.modules`).
- cfg-deployment's own `docs/decisions/adr-0013-external-app-dispatcher-api.md`
  anticipates this exact gap, unprompted: onboarding Dispatcher is meant to
  write "the first real strategy: `type: image` for the container artifact,
  plus a remote strategy for the IaC version" — i.e. they expect the
  artifact itself to be directly pinnable, not wrapped in a workload
  description.

## Alternatives considered and rejected

1. **Stretch `Module` to cover it** (relax `ModuleSpecModel.default_labels`
   to optional, treat it as a "reference-only" module with no deployer).
   Rejected: `ModuleServiceModel` is still full of Compose/Helm-only fields
   (`ports`/`mounts`/`healthcheck`/`depends_on`/`release_name`/
   `kubernetes_namespace`), and — more fundamentally — there is no
   namespace or topology component here to attach a Module *to*. The
   attachment mechanism itself doesn't fit, not just the field list.
2. **`kind: version` overlays a variable's value directly** (a
   `pins.variables` category keyed by variable name, overriding
   `constant`/`environment` values at resolution time). User's own
   objection: this doesn't match strata's convention — every other real
   thing (`provider`, `tenant`, `module`, a remote) is its own top-level,
   independently-referenceable document with an identity; a pin quietly
   overlaying an unrelated variable's declared value is a side-channel, not
   an identity reference. Set aside in favor of a real document kind.
3. **Promote `SourceModel` itself into a top-level, referenceable kind**
   (rather than a new kind that merely reuses its field shape). Rejected:
   `sync_source()` (confirmed by reading it directly) always takes a
   `SourceModel` **by value** and materialises it into the *caller's own*
   `build_path` — a Module's source and a Provisioner's source, even if
   identical, are fetched independently, each into their own directory.
   There's no existing "resolve once, many referencers share the result"
   notion to hang an identity off of; giving `SourceModel` real cross-
   document identity would force a real, undesigned decision (does a
   reference trigger its own independent sync anyway — in which case
   identity bought nothing — or does it materialise once and get reused,
   a genuine behavioural change to `sync_source()`/`build_workload_modules()`
   with zero real evidence requiring it). It also means touching two
   already-working, already-tested mechanisms (`Module.spec.source`,
   `Provisioner.source`) to become references instead of embedded values,
   for a benefit only the *new*, isolated `kind: artifact` needs. Reusing
   `SourceModel`'s **field shape** (see below) inside a small, separate
   kind gets the same shape benefit with zero blast radius on existing,
   working code.

## Direction being explored: a new `kind`

A lightweight, deploy-agnostic document — working name **`artifact`** —
whose only job is to be a pinnable, named reference to something external
(a container image today; possibly a chart or other identifier later),
independent of whether/how strata deploys it. Referenced by identity
(ADR-0015) from anywhere in the solution — a variable, a `Module` service,
future kinds — not attached to a namespace/topology at all, so it's usable
across every workspace that needs it, matching how `provider`/`tenant`
already work.

**Shape refinement (2026-09-25): reuse `SourceModel`'s pattern, not the
type itself.** `VersionPinsModel`'s own docstring confirms `pins.charts`
targets `SourceModel.chart_version` today — `SourceModel` has the right
*pattern* ("a selector field decides the mode, a version field on that
mode gets pinned"), which chart mode (`chart_name` + `chart_version`)
demonstrates. An image mode was first sketched as a literal third
`SourceModel` mode (`remote` + `image_name` + `image_tag`) — corrected
below.

**Shape correction (2026-09-25): don't reuse `SourceModel`'s `remote`
after all.** Digging into `remote`'s actual semantics: it's fetch-oriented
— `SolutionRemoteModel.fetch: strata|external` governs *who materialises
bytes on disk*, and strata never fetches a container image at all (no
`sync_source()`-equivalent for images; the image stays in the registry and
is pulled by Kubernetes/Docker/the Web App runtime at deploy time, never
by strata at build time). Forcing `remote` onto image mode would model a
fetch step that structurally cannot happen. Real evidence also confirms
registry credentials are **already solved, separately, today**:
`container_registry_url`/`_username`/`_password` are plain `constant`/
`store: github`-secret variables declared right alongside the image name/
tag — not a `SolutionRemoteModel`. The artifact document doesn't need to
own fetch/auth for that reason.

**Optional `integration` field instead, reusing the existing `sources`
capability.** `VALID_INTEGRATION_CAPABILITIES` already documents `sources`
as covering "`SolutionRemoteModel`-backed artifact sources (git/oci/helm
auth)", and `RemoteType.OCI`'s own docstring already treats an OCI registry
as serving container images — so an artifact referencing an `Integration`
for registry credentials (`integration: Annotated[PlatformName,
References(PlatformKind.INTEGRATION)] | None`, same field name/pattern as
`SolutionRemoteModel.integration`) reuses an existing capability rather
than inventing a new one. Kept optional and decoupled from `registry`
(a plain string, just the host) — real environment files don't use this
today (plain variables/secrets instead), so this is a supported upgrade
path, not a requirement forced onto every artifact.

Revised shape — no `remote`, no chart mode (real chart pins exist, but
every one targets a real `kind: module`'s own `SourceModel.chart_version`
— already fully served by the existing mechanism; not a gap this new kind
needs to cover, see the 2026-09-27 fixture-check correction below):

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: artifact
meta:
  name: dspapi_container
spec:
  image_name: "int-docker-test/src/acme.dispatcher.api"
  image_tag: ""                    # blank until known, same pattern as today's variable
  registry: "cdn-feeds.acme.com"    # free text, documentation only
  integration: null                # optional Integration (capability: sources) for registry auth
  description: "Dispatcher API container image (ADR-0013)"
  properties: null                 # free-form descriptive data (e.g. owning team, cost center) — never merged anywhere
  configuration: null              # raw passthrough data for whatever consumes this artifact
  custom: null                     # custom user-defined data for scripts/extensions
```

`properties`/`configuration`/`custom` added 2026-09-26 to match every
other kind's convention (`dict[str, Any] | None`, same field names/shapes
as `TenantSpecModel`/`ResourceSpecModel`) — unlike `Tenant`'s `properties`/
`custom` (a base layer merged into every deployment referencing it),
`Artifact`'s three fields have **no merge semantics** — there is nothing
downstream that merges an artifact's properties into anything else, they
are purely descriptive/passthrough, closer to `ResourceSpecModel.custom`'s
"custom user-defined data for scripts or extensions" treatment than
`TenantSpecModel.properties`'s merge behaviour.

`kind: version`'s new `pins.artifacts` (see Open questions below) keys
against this document's `meta.name`, mirroring how `pins.images` already
keys against `ModuleServiceModel.name`, and overlays **`image_tag` only**
— mirroring the existing precedent exactly (`chart_name` is never
overridden by a pin, only `chart_version` is; here `image_name` is
identity, `image_tag` is the pinned selection).

**The "combined `name:tag`" need (adminapp's real Terraform variable
shape, `adminapp_container_image_name_with_tag`) stays a resolution-time
concern, not a schema field** — whatever mechanism references this
artifact from a variable picks which sub-value it wants (`image_name`
alone, `image_tag` alone, or a synthesised `image_name:image_tag`), since
real Terraform roots disagree on the shape they want (split for
dispatcher/datahub_api/staging_api, combined for adminapp). That has to be
a per-variable choice at the reference site, not baked into the artifact
itself.

## Example usage — putting the whole design together

One artifact document, referenced two different ways (the two
consumption paths above), plus a version pin overlaying its tag:

```yaml
# artifact: the pinnable reference itself (kind: artifact)
apiVersion: strata.huybrechts.xyz/v2
kind: artifact
meta:
  name: dspapi_container
spec:
  image_name: "int-docker-test/src/acme.dispatcher.api"
  image_tag: "env_sbx11006400201_20260907.10"
  registry: "cdn-feeds.acme.com"
  description: "Dispatcher API container image (ADR-0013)"
```

```yaml
# environment.yaml: a variable sourced from the artifact (Path 2 — no
# strata-owned field exists on the external dispatcher_api provisioner)
spec:
  variables:
    - key: dspapi_container_image_tag
      store: artifact
      value: dspapi_container   # ArtifactModel.meta.name
      field: image_tag           # -> variables.auto.tfvars.json's dspapi_container_image_tag
```

```yaml
# namespace.yaml: a Compose/Helm service sourced from the same artifact
# directly (Path 1 — ModuleServiceModel owns its own image field)
spec:
  services:
    - name: dispatcher
      artifact: dspapi_container   # References(PlatformKind.ARTIFACT), not a literal image string
```

```yaml
# version.yaml: pinning the tag, with the audit trail kind: version exists for
apiVersion: strata.huybrechts.xyz/v2
kind: version
meta:
  name: prd
spec:
  pins:
    artifacts:
      dspapi_container:
        version: "env_sbx11006400201_20260907.11"
        status: current
        reviewed: 2026-09-25
```

Resolution for both consumption paths checks `deployment.spec.version` →
`pins.artifacts.dspapi_container` first; only when no pin exists does
either path fall back to the artifact document's own declared
`image_tag`.

**Resolved**: dedicated top-level `kind: artifact` — see "Identity/reuse"
in Open questions below for the full reasoning.

## Open questions — not settled, still thinking

- **Reference mechanism — resolved (2026-09-25): `store: artifact`
  (Value Supply Mechanism A), not a `${artifact:}` token (Mechanism B).**
  Reasoning:
  1. Mechanism B tokens (`${var:}`/`${secret:}`/`${feature:}`) exist
     *specifically because* deploy-time resolution is required — secrets
     and integration-backed values genuinely aren't known at build time.
     An artifact's `image_name`/`image_tag` are always fully known at
     build time (declared directly in the document, plus any
     `kind: version` pin — itself fully declared in-solution too). There
     is no technical reason to defer this to deploy time; doing so would
     be a regression (an already-available value made artificially
     unavailable until deploy).
  2. Mechanism B is reserved for embedding a token *inside* a field strata
     itself owns (a DNS record, a firewall rule) — our real need is a
     variable's **own entire value** equalling the artifact's field,
     which is exactly Mechanism A's shape (one store, one resolved value,
     one variable), not a substring token.
  3. **Near-zero schema change to `VariableStoreModel`.** `value`'s
     established meaning already covers this: "a literal for `constant`,
     an env var name for `environment`, a config path/key for
     integration-backed stores" — for `store: artifact`, `value` is
     simply the `ArtifactModel.meta.name`, no different in kind from
     every other store. The one real addition is a **new, narrowly-scoped
     `field` selector** (`Literal["image_name", "image_tag", "image_ref"]
     | None`), validated as "only valid when `store == artifact`" — the
     exact same pattern `default`/`generate`/`rotate` already use
     (`_validate_field_not_on_builtin`). Deliberately **not** a dotted
     string packed into `value` (e.g. `"dspapi_container.image_tag"`):
     that would need inventing parsing/escaping rules (what if an artifact
     name itself contains a `.`?) and would give up real schema-level
     validation (`field` as a `Literal` is typo-checked at load time by
     Pydantic; a dotted string is only checked, if at all, at resolution
     time).
  4. Supported `field` values: `image_name`, `image_tag` (the two real
     declared fields — `image_name` is identity, never pin-overlaid,
     matching the `chart_name`-fixed precedent; `image_tag` consults
     `kind: version`'s `pins.artifacts` first, falling back to the
     document's own declared value), plus one synthesised `image_ref`
     (`f"{image_name}:{image_tag}"`) to cover adminapp's real "combined"
     Terraform variable shape, computed by the resolver, not a stored
     field.
  5. **Resolution flow mirrors `resolve_tenant()` exactly**:
     `deployment.spec.version` (already exists —
     `Annotated[PlatformName, References(PlatformKind.VERSION)] | None`,
     confirmed in `deployment_model.py`) → look up the `VersionModel` →
     check `pins.artifacts[artifact_name]` → else fall back to the
     artifact document's own declared `image_tag`. Same shape as the
     tenant-defaults lookup already built this session, just targeting a
     different kind.
  6. Resolved **once, at build time**, into `build_value_references()`'s
     existing pipeline — same treatment as `constant`/`environment`,
     appearing in `resolved.yaml` with a real, non-`None` `value` (not
     secret-shaped, nothing to hide).
  7. Not built yet. Needs: (a) add `"artifact"` to `VariableStoreType`
     and to `BUILTIN_VARIABLE_STORE_TYPES` (resolvable without an
     `Integration` — it's an in-solution document lookup, same bar
     `constant`/`environment` already clear); (b) add the new `field:
     Literal["image_name", "image_tag", "image_ref"] | None` field to
     `VariableStoreModel`, validated as only-valid-for-`store: artifact`
     (same shape as the existing `validate_default_not_on_builtin`); (c) a
     new resolution function in `value_controller.py`, structurally
     parallel to `resolve_tenant()`; (d) wire it into
     `build_value_references()`'s per-store dispatch.
- **Refinement (2026-09-25): two consumption paths, not one — checked
  every model for an existing "image"-like field first.**
  `ModuleServiceModel.image: str | None` is the *only* one in the entire
  v2 schema (confirmed by grep across `src/strata/models/*.py`). That
  splits the reference mechanism into two genuinely different situations:

  1. **`ModuleServiceModel` (Compose/Helm workloads strata itself
     renders)** — this is a strata-owned model field, so a direct
     reference fits cleanly: add
     `artifact: Annotated[PlatformName, References(PlatformKind.ARTIFACT)]
     | None`, mutually exclusive with the existing `image: str | None`
     (same "exactly one of X/Y" pattern `ModuleSpecModel.compose_file`/
     `.services` already uses). No variable, no store, no `field`
     selector — the Compose/Helm builder resolves it directly when
     reading the service, since it already fully owns that field. Named
     `artifact`, not `image_ref`/`artifact_ref` — every existing
     `References(PlatformKind.X)` field in the codebase (19 checked, zero
     exceptions) is named either exactly after the kind
     (`module`/`tenant`/`version`/`integration`/`resource`) or a clearer
     relationship word only when the bare kind name would be ambiguous
     (`extends`→DEPLOYMENT, `workspace`→WORKSPACE, `dns_zones`→DNS) —
     never a `_ref` suffix.
  2. **The real motivating case — dispatcher_api/datahub_api's images**
     — has **no strata-owned field to attach a reference to at all**. The
     consuming thing is an opaque *external* Terraform module strata has
     no model for; its inputs are arbitrary variables the module itself
     defines (`dspapi_container_image_name`,
     `adminapp_container_image_name_with_tag`, ...). There is no
     `ProvisionerModel.image`-shaped field to add, because strata has no
     opinion about what that module needs — which is exactly why
     `variables:` exists as a generic escape hatch in the first place.
     `store: artifact` (points 1-7 above) is not an indirection to avoid
     here; it is the *only* channel that can reach an arbitrary,
     externally-defined Terraform variable name.

  Both are needed, for two different reasons, not redundant with each
  other — one because strata owns the target field, one because it
  structurally cannot.
- **Shape — resolved (2026-09-25), corrected same day**: standalone spec,
  not a reused `SourceModel` mode — `image_name`, `image_tag`, `registry`
  (plain string), optional `integration` (capability: `sources`),
  `description`. No `remote`/fetch semantics (strata never fetches an
  image) and no chart mode (real chart pins exist — see the fixture-check
  correction below — but every one targets a real `kind: module`'s own
  `SourceModel.chart_version`, already fully served). See above for the
  full reasoning.
- **Identity/reuse — resolved (2026-09-25)**: dedicated top-level
  `kind: artifact`. Real-multiplicity evidence tipped this: at least 7
  distinct real image references exist already across dispatcher/datahub/
  staging/adminapp/binocs/sci (see Real evidence above), several
  explicitly meant to share one pin value — a real, growing registry, not
  a single embedded one-off. `pins.artifacts` (below) needs a flat,
  globally-unique name to key against, which only a real `DocumentIndex`
  entry (`(PlatformKind.ARTIFACT, meta.name)`) provides — an embedded
  `dict[PlatformName, ArtifactSpecModel]` field would have the same
  cross-workspace-uniqueness problem raised earlier for embedding on
  `Provisioner`/`Workspace`.
- **`kind: version` wiring — resolved (2026-09-25): new `pins.artifacts`
  category, not an overload of `pins.images`.** Keeps `pins.images`
  exactly as-is (unambiguously `ModuleServiceModel.name`) — no
  disambiguation logic needed, no risk to the existing, working
  Compose/Helm image-pinning path. `VersionPinsModel` gains a fourth
  category alongside `images`/`charts`/`remotes`:

  ```python
  PIN_CATEGORIES = ("images", "charts", "remotes", "artifacts")

  class VersionPinsModel(PlatformBaseModel):
      ...
      artifacts: dict[PlatformName, VersionPinModel] | None = Field(
          default=None,
          description="Artifact image tag pins. Key = ArtifactModel.meta.name; "
          "overlays spec.image_tag only (image_name is identity, never pinned).",
      )
  ```

  `iter_pins()` (already a generic loop over `PIN_CATEGORIES`) picks this
  up automatically — no change needed there beyond the tuple. The "four
  images share one build tag" real case (dispatcher/datahub_api/
  staging_api/adminapp) is expressed as four separate `pins.artifacts`
  entries with the identical `version` value — visible/auditable
  duplication (each can still carry its own `status`/`reason`/`reviewed`
  independently, e.g. one held back while the other three move on) rather
  than a new "linked pins" indirection mechanism, which nothing in the
  real evidence requires yet.
- **Real fixture check — done (2026-09-27), earlier claim was wrong.**
  `haven/versions/prd.yaml` is a real, production `kind: version` document
  with 9 real `pins.images` entries and 6 real `pins.charts` entries, full
  `status`/`reason`/bump commentary matching `VersionPinModel` exactly
  (`db: ... # HELD — postgres majors need pg_upgrade/dump-restore`,
  `gatus: 1.0.0 # UNVERIFIED — could not confirm...`, etc.) — the "zero
  confirmed real usage" claim below was wrong; `pins.images`/`pins.charts`
  are both real, working, and heavily used. This doesn't reopen chart mode
  for the new `kind: artifact`, though — every real chart pin targets a
  real `kind: module` document's own `SourceModel.chart_version` (haven's
  `cert-manager`/`gatus`/`homarr`/`immich`/`jellyfin`/`nextcloud`, all
  real Helm charts strata's own Helm builder installs) — a Path-1-like
  case (strata owns the field) already fully served by the existing
  mechanism, not a Path-2 gap the way images were. If anything this
  strengthens confidence in `pins.artifacts` mirroring the same overlay
  pattern (`pins.images`/`.charts` proven correct and battle-tested in
  real production use).
- **Naming — resolved (2026-09-27): keep `artifact`.** The design ended
  up generic on purpose (`properties`/`configuration`/`custom` match every
  other kind's convention; the doc's own original framing was always "can
  be many things — a container image, a chart, maybe other identifiers
  later"). A narrower name (`image-ref`, `container-image`) would undersell
  a shape already built to generalize.
- Out of scope for now: this is not a `build_run` gap (build_run's own
  remaining work is exhausted, see [build-command.md](../work/build-command.md)) —
  it's `version`/resolution scope, and `deploy run` doesn't exist yet
  either, so there's no urgency to settle the shape before real deploy-time
  resolution work begins.

## Implementation Plan

1. ✅ **`ArtifactModel` (new model + service + kind registration)** — done
   2026-09-27. `PlatformKind.ARTIFACT`, `src/strata/models/artifact_model.py`
   (`ArtifactMetaModel`/`ArtifactSpecModel`/`ArtifactModel`),
   `src/strata/services/artifact_service.py`, registered in
   `solution_controller.py`'s `SERVICE_BY_KIND`. 6 new model tests, plus an
   end-to-end sanity check confirming `open_solution()` discovers and
   indexes a real `kind: artifact` document. Full check suite green (1051
   tests, mypy 105 files, 0 broken import-linter contracts).
2. ✅ **`ModuleServiceModel.artifact` (native Compose/Helm path)** — done
   2026-09-27. Added `artifact: Annotated[PlatformName,
   References(PlatformKind.ARTIFACT)] | None`, mutually exclusive with
   `image: str | None` (a new `model_validator`, same pattern as
   `compose_file`/`.services`). 3 new tests (accepts artifact reference,
   rejects both set, allows neither). Full check suite green (1054 tests).
3. ✅ **`store: artifact` + `field` selector (external-provisioner path)**
   — done 2026-09-27. `VariableStoreType.ARTIFACT` added and included in
   `BUILTIN_VARIABLE_STORE_TYPES` (in-solution document lookup, no
   `Integration` needed — same bar `constant`/`environment` already
   clear); new `field: Literal["image_name", "image_tag", "image_ref"] |
   None` on `VariableStoreModel`, validated as only-valid-for-`store:
   artifact` (a new `model_validator`, mirrors the existing
   `default`-not-on-builtin pattern — and `default` itself is now
   correctly rejected on `store: artifact` too, since it's a built-in
   lookup with nothing to seed). 4 new tests. Full check suite green
   (1058 tests).
4. ✅ **`VersionPinsModel.artifacts` pin category** — done 2026-09-27.
   `PIN_CATEGORIES` gains a fourth entry (`"artifacts"`); new
   `artifacts: dict[PlatformName, VersionPinModel] | None` field, same
   shorthand/structured/rationale rules as every other category (inherited
   for free from `VersionPinModel`). `iter_pins()` needed no change
   (already a generic loop). 3 new/updated tests, including one confirming
   `pins.images`/`pins.artifacts` don't collide even with the same key
   name. Full check suite green (1060 tests).
5. ✅ **Resolution wiring — Path 2 (`store: artifact`) done 2026-09-27;
   Path 1 (`ModuleServiceModel.artifact` → Compose/Helm) done 2026-10-06.**
   `resolve_artifact()`/`resolve_artifact_field()` added to
   `value_controller.py`, mirroring `resolve_tenant()` — `image_name` read
   straight off the artifact (never pin-overlaid); `image_tag` checks
   `deployment.spec.version` → `VersionModel.spec.pins.artifacts[name]`
   first, falls back to the artifact's own declared value;
   `image_ref` synthesises `"{image_name}:{image_tag}"` (bare `image_name`
   when the tag is blank). Also added the "field is *required* when
   `store: artifact`" validation `store_model.py` was missing (a real gap
   found while implementing this phase — Phase 3 only rejected `field` on
   *other* stores, never required it here). `build_value_references()`
   gained optional keyword-only `context`/`deployment` params, wired into
   its per-store dispatch for variables only (never features/secrets);
   `build_controller.py`'s one call site passes both through. 10 new
   tests, full check suite green (1070 tests).

   **Path 1 — done 2026-10-06, per the "Path 1 Resolution Wiring —
   Solution Design" section below.** `resolve_artifact_services()` added
   to `workload_controller.py`, mirroring `apply_version_pins()`'s own
   `model_copy()` overlay shape; wired into `build_workload_modules()`'s
   per-reference loop right after `apply_version_pins()`.
   `build_workload_modules()` gained optional keyword-only `context`/
   `deployment` params (default `None`, graceful no-op — same precedent
   `build_value_references()`'s own params already established, so none of
   the 18 existing test call sites needed updating); `build_controller.py`'s
   one call site passes both through (both already in local scope). As
   corrected below, `compose.py`/`helm.py` needed **zero** changes — Helm
   never read `.image`/`.artifact` at all, and Compose already only ever
   needed `.image` populated with a real string. 9 new tests. Full check
   suite green: mypy (146 files), ruff, import-linter (0 broken
   contracts), pytest (2176 passed).
6. ✅ **Full check suite + docs** — done 2026-09-27. Wrote
   [ADR-0026](../decisions/0026-pinnable-artifact-references.md)
   (`partially-implemented` — Path 2 done, Path 1 deferred with reasoning);
   added `artifact` to `v2-schema-overview.md`'s kind catalog; this doc's
   status updated to match the ADR. Final full check suite: 1070 tests,
   mypy 105 files clean, ruff clean, 0 broken import-linter contracts.
   **Superseded 2026-10-06**: Path 1 is now also done (item 5 above) —
   ADR-0026's status should be updated from `partially-implemented` to
   `implemented` the next time that ADR is touched.

## Path 1 Resolution Wiring — Solution Design (2026-10-06)

**✅ Implemented 2026-10-06 — matches this design exactly, see
"Implementation" at the end of this section.** Kept as the original design
record below (unchanged) for history.

Design only (at the time this section was written) — sizes the remaining
work precisely against real code (correcting Implementation Plan item 5's
original, less precise framing above), after directly reading
`compose.py`/`helm.py`/`workload_controller.py`/`build_controller.py`.

### Correction to the original deferral note

The original note said resolving `ModuleServiceModel.artifact` "requires...
passing a resolved image lookup down through `prepare_namespace()`'s
signature on *both* `ComposeIntegration` and `HelmIntegration`." Checked
directly — this overstates it:

- `ComposeIntegration._render_namespace_services()` (`compose.py`) reads
  only `service.image` (`if service.image: entry["image"] = service.image`)
  — never `.artifact`.
- `HelmIntegration._render_values()` (`helm.py`) reads
  `.environment`/`.mounts`/`.configuration` **and never reads `.image` or
  `.artifact` at all** — matches `ModuleServiceModel.image`'s own field
  docstring ("Omit for Helm charts that define their own image"). Helm
  needs zero changes for Path 1 under any design.

Both integrations only ever need `service.image` populated with a real
string — nothing downstream needs to know `.artifact` was ever set. The
fix belongs entirely one layer up, in the controller that already
resolves everything else before an integration ever runs (ADR-0021 D2) —
not in either integration.

### The design: resolve `.artifact` into `.image`, once, before grouping

`workload_controller.apply_version_pins()` already does the exact shape
this needs — resolve something external, overlay it onto a service via
`model_copy()`, return a new `ModuleModel` — for `pins.images`/`pins.charts`.
Path 1 is the same shape, a different source:

```python
def resolve_artifact_services(
    module: ModuleModel, context: SolutionContext, deployment: DeploymentModel
) -> ModuleModel:
    """Resolve every service.artifact reference into service.image.

    Mirrors apply_version_pins()'s own overlay shape. Clears `.artifact`
    on the returned service once resolved, so every render function keeps
    checking only `.image` — no second field for compose.py/helm.py to
    learn about.
    """
    services = module.spec.services or []
    overrides: dict[str, Any] = {}
    for service in services:
        if service.artifact is None:
            continue
        image_ref = resolve_artifact_field(context, deployment, str(service.artifact), "image_ref")
        if image_ref is None:
            # Unreachable in a require_valid()-passed solution — .artifact is an
            # unconditional References(PlatformKind.ARTIFACT) field, already
            # guaranteed to resolve by Phase 2 validate_references(). Same
            # defensive-backstop treatment as resolve_module()'s own UsageError.
            raise UsageError(
                f"Module '{module.meta.name}' service '{service.name}': artifact reference did not resolve."
            )
        overrides[service.name] = service.model_copy(update={"image": image_ref, "artifact": None})

    if not overrides:
        return module
    return module.model_copy(
        update={"spec": module.spec.model_copy(update={"services": [overrides.get(s.name, s) for s in services]})}
    )
```

Called from `build_workload_modules()`'s per-reference loop
(`workload_controller.py`, right after `apply_version_pins()`, same spot),
so a module can have both a version pin *and* an artifact reference
resolved in the same pass — order doesn't matter between them since they
touch different fields (`pins.images` only overlays a service that
already has `.image` set; `.artifact` is mutually exclusive with `.image`
on the same service, so the two overlays never compete for the same field
on one service).

### Why `field="image_ref"`, not `"image_name"`/`"image_tag"` separately

`.image`'s own real shape is one string, `"name:tag"` (`image:
caddy:2-alpine`) — exactly what `resolve_artifact_field()`'s synthesised
`image_ref` already produces (`f"{image_name}:{image_tag}"`, bare
`image_name` when the tag is blank). No new formatting logic needed; this
is the one `field` value Path 1 ever needs to request.

### Signature changes — minimal, additive only

- `build_workload_modules()` gains two new required params: `context:
  SolutionContext`, `deployment: DeploymentModel` — added alongside the
  existing `index`/`root`/`remotes` (not replacing them, despite `context`
  already wrapping `index`/`root` — a larger signature simplification is
  real but out of scope for this fix; zero blast radius preferred over a
  larger refactor).
- `build_controller.py`'s one call site (the `for namespace in
  graph.namespaces.values(): build_workload_modules(...)` loop) already
  has both `context` and `deployment` in local scope (`deployment =
  resolve_deployment(context, deployment_name, version_pin=pin)` runs well
  before this loop) — passing them through is the only change needed at
  the call site itself.
- `compose.py`/`helm.py` — **no changes**, per the correction above.

### Testing surface

A new module fixture with one service using `.artifact` instead of
`.image`, a real `Artifact` document it resolves against, run through
`build_workload_modules()` end to end — assert the rendered
`docker-compose.yml`'s `image:` key equals the artifact's resolved
`image_ref`. Mirrors the shape of `apply_version_pins()`'s own existing
test file (same fixture-and-assert pattern, different source field).

### Still gated on real usage, not on cost

This sizing doesn't change the original call to defer — zero real haven
module sets `.artifact` today (every real module still uses a plain
`image:` literal). It does mean that *when* this is picked up, it is a
small, well-understood, single-function change plus two call-site
parameters — not the "both integrations' signatures" effort the original
deferral note implied.

### Implementation (2026-10-06)

Built exactly as designed above, with one refinement: `context`/
`deployment` are optional keyword-only params on `build_workload_modules()`
(default `None`, graceful no-op when either is unset) rather than required
— matching `build_value_references()`'s own existing precedent for the
same pair of params, so none of the 18 existing `build_workload_modules()`
test call sites needed updating.

- `resolve_artifact_services()` added to `workload_controller.py`, right
  after `apply_version_pins()` — same `model_copy()` overlay shape, called
  from `build_workload_modules()`'s per-reference loop immediately after
  `apply_version_pins()`.
- `build_workload_modules()` gained `context: SolutionContext | None = None`
  and `deployment: DeploymentModel | None = None` keyword-only params.
- `build_controller.py`'s one call site now passes `context=context,
  deployment=deployment` — both were already in local scope, no new
  plumbing needed above the call site.
- `compose.py`/`helm.py` — **zero changes**, confirmed.
- 9 new tests (5 unit tests for `resolve_artifact_services()` covering the
  `None`-context/`None`-deployment/no-`.artifact` no-op paths, the overlay-
  and-clear path, and the defensive-backstop `UsageError`; 1 new end-to-end
  `build_workload_modules()` test confirming a resolved artifact reaches
  the real rendered `docker-compose.yml`'s `image:` key). Full check suite
  green: mypy (146 files), ruff, import-linter (0 broken contracts), pytest
  (2176 passed).

## Related Decisions

- [ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md) —
  Value bindings (`${var:}`/`${secret:}`/`${feature:}` tokens), the
  precedent for any new token-based reference mechanism considered above.
- [ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md) —
  identity-based addressing (`(kind, meta.name)` indexing), the pattern a
  new `artifact` kind would follow.
- [ADR-0023](../decisions/0023-build-output-rendering.md) — the `tenant`
  Terraform category precedent (also a "document referenced by identity,
  projected into build output" shape).
- [ADR-0026](../decisions/0026-pinnable-artifact-references.md) — the
  decision this whole design doc's history led to; this doc is its full
  design record, kept living/append-only per `docs/decisions/README.md`'s
  own rule that build-out history belongs in `docs/design/`, not the ADR.

## Changelog

- 2026-09-25: Created. Triggered by validating `build-command.md`'s
  `tenant`/`modules` gap-table rediscovery against cfg-deployment's
  real `dspapi_container_image_name`/`_tag` variables — found `kind:
  version`'s pin categories can't reach a plain variable's value, `Module`
  doesn't fit (no attachment point, wrong field shape), and a
  pin-overlays-a-variable-directly mechanism was rejected as inconsistent
  with strata's identity-reference convention. No schema decided yet —
  captured here to keep thinking before writing any ADR/code.
- 2026-09-25: **Shape refinement.** Confirmed `VersionPinsModel.charts`
  already targets `SourceModel.chart_version` today — so `SourceModel`
  (already used by `Module`/`Provisioner`) is a strong candidate for the
  artifact's own shape: extend it with a third "image mode"
  (`image_name`+`image_tag`, alongside `remote`), unifying how
  `pins.images`/`pins.charts` both resolve instead of `images` staying a
  special-cased flat string. Narrowed the open questions to what's still
  genuinely unresolved: identity/reuse (new top-level `kind: artifact` vs.
  an embedded `dict[PlatformName, SourceModel]` field, since no real
  evidence yet shows the *same* image shared across workspaces) and
  whether `pins.images`/`pins.charts` should collapse into one category.
- 2026-09-25: **Correction — this is not a one-off case.** Re-checked
  `stacks/ring/environment-datahub.yaml` and `environment-sci.yaml`
  directly: at least 7 distinct real image references exist today
  (dispatcher, datahub_api, staging_api, adminapp, binocs_api — recorded
  but disabled, sci), not just dispatcher_api. One file's own comment
  confirms four of them are meant to share exactly one pin value ("all
  four images publish under the same feed/prefix and share one tag per
  build"). This retires the earlier "arguably over-engineered for one
  confirmed use case" concern raised against a dedicated `kind: artifact`
  — a real, small, growing registry with cross-artifact sharing is
  exactly what a flat, named, listable top-level kind is for.
- 2026-09-25: **Shape correction — drop `SourceModel`/`remote` reuse.**
  Designing the actual fields surfaced that `SourceModel.remote` is
  fetch-oriented (`SolutionRemoteModel.fetch: strata|external` = "who
  materialises bytes on disk"), and strata never fetches a container image
  at all — the registry pulls it at deploy time, not strata at build time.
  Real evidence also confirms registry credentials are already solved
  separately today (plain `constant`/`store: github`-secret variables,
  not a `SolutionRemoteModel`). Replaced the `SourceModel`-mode sketch with
  a standalone spec: `image_name`, `image_tag`, `registry` (plain string),
  and an optional `integration` field (`Annotated[PlatformName,
  References(PlatformKind.INTEGRATION)]`) reusing the existing `sources`
  capability — same pattern as `SolutionRemoteModel.integration`, no new
  capability needed. Pinning overlays `image_tag` only, mirroring
  the `chart_name`-fixed/`chart_version`-pinned precedent. Chart mode
  dropped entirely (zero real chart-pin evidence). The "combined
  `name:tag`" shape adminapp's real Terraform root wants stays a
  resolution-time formatting concern, not a schema field.
- 2026-09-25: **Identity/reuse and `kind: version` wiring resolved.**
  Went with a dedicated top-level `kind: artifact` (not an embedded field
  — `pins.artifacts` needs a flat, globally-unique name to key against,
  which only a real `DocumentIndex` entry provides) and a new, separate
  `VersionPinsModel.artifacts` pin category (not an overload of
  `pins.images`) — keeps the existing `ModuleServiceModel`-targeting
  `pins.images` completely unchanged, no disambiguation logic needed.
  `PIN_CATEGORIES` gains a fourth entry; `iter_pins()` needs no change
  (already a generic loop). The real "four images share one build tag"
  case is expressed as four separate `pins.artifacts` entries with an
  identical `version` value — visible, auditable duplication (each still
  independently carries `status`/`reason`/`reviewed`) rather than a new
  "linked pins" indirection mechanism nothing in the real evidence
  requires yet.
- 2026-09-25: **Reference mechanism resolved: `store: artifact`
  (Mechanism A), not a `${artifact:}` token (Mechanism B).** Mechanism B
  exists specifically for values that genuinely aren't known until deploy
  (secrets, integration-backed lookups) and for embedding a token inside a
  field strata itself owns — neither applies here: an artifact's fields
  are always fully known at build time, and the real need is a variable's
  *entire* value equalling the artifact's field, not a substring token.
  `store: artifact` needs zero schema change to `VariableStoreModel` —
  reuses the same generic `value: Any` overloading every other store type
  already uses, as a dotted `"<artifact_name>.<field>"` reference
  (`.image_name`, `.image_tag`, or a synthesised `.image_ref` for
  adminapp's combined shape). Resolution mirrors `resolve_tenant()`
  exactly: `deployment.spec.version` (already exists) → `VersionModel` →
  `pins.artifacts[name]` → fallback to the artifact's own declared value.
  Resolved once at build time into `build_value_references()`'s existing
  pipeline, same as `constant`/`environment` — real, visible value in
  `resolved.yaml`, nothing secret-shaped to hide.
- 2026-09-25: **Reference mechanism refined — two paths, not one, plus a
  correction to point 3 above.** Grepped every model for an existing
  "image"-like field: `ModuleServiceModel.image` is the only one in the
  entire v2 schema. That means `store: artifact` isn't the *only*
  reference mechanism needed — `ModuleServiceModel` gets a direct
  `artifact: Annotated[PlatformName, References(PlatformKind.ARTIFACT)]
  | None` field instead (mutually exclusive with `image: str | None`), no
  variable/store involved at all, since Compose/Helm's own image field is
  strata-owned. `store: artifact` remains necessary — and is the *only*
  option — for the real motivating case (dispatcher_api/datahub_api),
  since those images are consumed by an opaque external Terraform module
  with no strata-owned field to attach a reference to; that's exactly why
  `variables:` exists as a generic escape hatch. Both mechanisms are
  needed, each for a different, non-overlapping reason.
- 2026-09-26: **Added `properties`/`configuration`/`custom` to match every
  other kind's convention** (`dict[str, Any] | None`, same field
  names/shapes as `TenantSpecModel`/`ResourceSpecModel`) — with no merge
  semantics, unlike `Tenant`'s (which base-layer-merge into every
  referencing deployment); purely descriptive/passthrough, closer to
  `ResourceSpecModel.custom`'s treatment. Added a full "Example usage"
  section showing one artifact document consumed both ways (a `store:
  artifact` variable for the external-provisioner case, a
  `ModuleServiceModel.image_ref` for the strata-owned Compose/Helm case)
  plus a `kind: version` pin overlaying its tag, tying every settled piece
  of this design together in one place.
- 2026-09-27: **Naming correction: `artifact`, not `image_ref`.** Checked
  every existing `Annotated[PlatformName, References(PlatformKind.X)]`
  field in the codebase (19 matches, zero exceptions): each is named
  either exactly after the kind it targets (`module`, `tenant`, `version`,
  `integration`, `resource`) or a clearer relationship word only when the
  bare kind name would be ambiguous (`extends`→DEPLOYMENT,
  `workspace`→WORKSPACE, `dns_zones`→DNS). No `_ref`-suffixed field exists
  anywhere. Renamed `ModuleServiceModel.image_ref` to `artifact` to match.
- 2026-09-27: **Naming (kind) resolved: keep `artifact`.** Design ended up
  generic on purpose (`properties`/`configuration`/`custom` match every
  other kind; original framing was always "can be many things"); a
  narrower name would undersell that. **Real fixture check done, earlier
  claim corrected**: `haven/versions/prd.yaml` is a real production
  `kind: version` document with 9 real `pins.images` + 6 real
  `pins.charts` entries (full `status`/`reason` commentary matching
  `VersionPinModel` exactly) — the "zero confirmed real usage" claim was
  wrong. Doesn't reopen chart mode for `kind: artifact` though: every real
  chart pin targets a real `kind: module`'s own `SourceModel.chart_version`
  (haven's cert-manager/gatus/homarr/immich/jellyfin/nextcloud, all real
  Helm charts strata's own builder installs) — already fully served,
  Path-1-like, not a Path-2 gap. If anything this strengthens confidence
  in `pins.artifacts` mirroring a proven, battle-tested pattern.
- 2026-09-27: **Implementation complete through Phase 6 (Path 2 fully
  built; Path 1 deliberately deferred).** All 6 implementation phases
  done: `ArtifactModel`/`ArtifactService`/kind registration;
  `ModuleServiceModel.artifact` (validates, round-trips — resolution
  wiring deferred, see below); `store: artifact` + `VariableStoreModel.
  field` (also found and fixed a real gap during implementation: `field`
  is now *required*, not just restricted, on `store: artifact`);
  `VersionPinsModel.artifacts`; `resolve_artifact()`/
  `resolve_artifact_field()` in `value_controller.py` wired into
  `build_value_references()`/`build_controller.py`. Wrote
  [ADR-0026](../decisions/0026-pinnable-artifact-references.md)
  (`partially-implemented`) and added `artifact` to `v2-schema-overview.
  md`'s kind catalog. Final check suite: 1070 tests, mypy 105 files clean,
  ruff clean, 0 broken import-linter contracts. Path 1's Compose/Helm
  rendering wiring remains open, explicitly not built — zero real haven
  module sets `.artifact` today (every real module still uses a plain
  `image:` literal) — tracked in ADR-0026's `## Remaining Work`.
- 2026-09-27: **Full-review finding: `store: artifact`'s `value` has zero
  cross-reference validation — resolved the same day.** Found while
  reviewing this feature and `deploy run` together end to end
  (docs/work/deploy-command.md's Remaining Work item 9 has the full
  write-up). Fixed with a new `EnvironmentService.
  validate_artifact_references()`, wired into `semantic_checks.py` as an
  8th cross-document check — the same precedent `WorkspaceService.
  validate_topology_references()` already established for a
  conditionally-meaningful field `references.py`'s generic walker can't
  check itself. 6 new tests. Full check suite green (1128 tests).- 2026-10-06: **Path 1 re-sized, corrected, and fully designed — still not
  built.** Direct request to check Implementation Plan item 5's deferral
  note against real code. Found it overstated the cost: `helm.py`'s
  `_render_values()` never reads `.image`/`.artifact` at all (only
  `.environment`/`.mounts`/`.configuration`), so Helm needs zero changes
  under any design — the "both integrations' `prepare_namespace()`
  signatures" framing was wrong. Added a full "Path 1 Resolution Wiring —
  Solution Design" section: a new `resolve_artifact_services()` mirroring
  `apply_version_pins()`'s own `model_copy()` overlay shape, called from
  `build_workload_modules()`'s existing per-reference loop; confirmed
  `build_controller.py`'s one call site already has both `context` and
  `deployment` in local scope, so only two new parameters are needed
  end to end; confirmed `.artifact` is an unconditional
  `References(PlatformKind.ARTIFACT)` field (already guaranteed to
  resolve by Phase 2 `validate_references()`, unlike `store: artifact`'s
  conditionally-meaningful `value`), so a resolution failure here is a
  defensive-backstop `UsageError` (matching `resolve_module()`'s own
  precedent), never a graceful skip. `compose.py`/`helm.py` need zero
  changes either way. Still gated on real usage (zero real haven module
  sets `.artifact` today) — this is a re-sizing and a ready-to-implement
  design, not a decision to build it now.
- 2026-10-06: **Path 1 implemented** ("do that otherwise haven is not
  going to be able to use that"). Built exactly per the "Path 1 Resolution
  Wiring — Solution Design" section's design, with one refinement:
  `context`/`deployment` made optional (default `None`, graceful no-op)
  rather than required, matching `build_value_references()`'s own existing
  precedent for the same pair of params — avoided touching any of the 18
  existing `build_workload_modules()` test call sites. New
  `resolve_artifact_services()` in `workload_controller.py`; `compose.py`/
  `helm.py` confirmed to need zero changes, as the design predicted. 9 new
  tests (5 unit + 1 end-to-end `build_workload_modules()` test confirming
  a resolved artifact reaches the real rendered `docker-compose.yml`, plus
  3 no-op/graceful-skip cases). Full check suite green: mypy (146 files),
  ruff, import-linter (0 broken contracts), pytest (2176 passed). This
  doc's status updated to `implemented`; ADR-0026 still says
  `partially-implemented` and should be updated the next time it's
  touched.