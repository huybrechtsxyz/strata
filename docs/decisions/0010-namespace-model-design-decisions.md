# Namespace Model — v2 Design Decisions

- Status: partially-implemented — model and thin service ported; file-existence
  Phase 2 checking deliberately not ported (see Remaining Work)
- Date: 2026-09-21
- Revised: 2026-09-21 — `NamespaceModuleModel` replaced by the shared
  `ModuleReferenceModel` (common_models.py), also used by
  `TopologyComponentModel.modules`. See
  [ADR-0011](0011-topology-and-provisioning-decoupling.md).
- Related: [ADR-0002](0002-requirement-interface-injection-grant-lessons-from-v1.md)
  (Requirement rejection — applied here), [ADR-0009](0009-module-model-design-decisions.md)
  (Module — a namespace is a named collection of modules; also the source of
  the path-traversal pattern reused here), [ADR-0011](0011-topology-and-provisioning-decoupling.md)
  (shared `ModuleReferenceModel` decision)

## Context and Problem Statement

`NamespaceModel` (`src/strata/models/namespace_model.py`,
`src/strata/services/namespace_service.py`) is the seventh v2 kind — a named
collection of modules (plus optional lifecycle hooks), the grouping level
directly above `Module`.

## Decisions

### 1. `spec.references`/`NamespaceReferenceModel` not ported

Same removal as every other kind (ADR-0002). Namespace's `references` field
was already the least-used of the pattern (no per-item Value bindings inside
`NamespaceModuleModel` reference it — it existed for forward-looking
consistency with other kinds, not because anything in Namespace itself
needed it).

### 2. `file` — added path-traversal protection

Not in v1: `file` (a reference to a module's YAML file, e.g.
`config/myapp/modules/template-module.yaml`) had no relative-path validation
at all. Following the same finding just made for `ModuleFileModel`
(ADR-0009 Decision 7), added the same check — rejects absolute paths and
`..`, with the same `@reponame/...` cross-repo handling (only the part after
the repo-name segment is checked). This is a real, not hypothetical, gap:
v1's own `NamespaceService._validate_dynamic()` resolves `file` against a
work path + repo map, meaning an unchecked `..` could already escape the
intended directory in v1 today.

Initially ported as a duplicate inline copy of `ModuleFileModel`'s check;
reviewing the two side by side (this ADR's own review) found the exact same
logic duplicated verbatim in both files, so it was extracted into one shared
`validate_file_ref_no_traversal()` (common_models.py) that both
`ModuleFileModel` and the (then-named) `NamespaceModuleModel` called — no
behavior change, pure DRY cleanup.

### 3. `type`/`lifecycle`/`modules`/validators ported unchanged

`NamespaceType` (dedicated/shared), the "must have lifecycle and/or modules"
check (with its `warnings.warn()` when no modules are present — kept as a
non-fatal `UserWarning`, matching v1), and unique-module-name checking are
all ported as-is — no ADR-0002-related changes needed since none of these
touch Value bindings.

### 4. `NamespaceService._validate_dynamic()` not ported

v1's version resolved `modules[].file` against a work path and a cross-repo
`repo_map` built from `ConfigurationModel`/registered repositories —
filesystem-existence checking, not schema validation. That machinery
(repo registration/resolution, build work paths) doesn't exist in v2 yet.
`NamespaceService` is Phase-1-only, matching every other kind's service so
far.

### 5. `PlatformKind.NAMESPACE` added

`NAMESPACE = "namespace"` added to `PlatformKind` (eighth member).

### 6. `NamespaceModuleModel` replaced by the shared `ModuleReferenceModel` (2026-09-21)

When designing `TopologyComponentModel.modules` (ADR-0011 — attaching a
module directly to a resource, e.g. Function App code onto its Function App),
the question "how does this relate to Namespace's modules?" surfaced a real
duplication risk: both are ultimately "a pointer to a `Module` document plus
placement metadata," differing only because v1's `WorkspaceModuleReferenceModel`
(the resource-attachment case) happened to gain `slot_type`/`enabled`/
`configuration` while `NamespaceModuleModel` never did — an accident of where
v1 first needed them, not a genuine conceptual difference. Rather than add a
second, slightly richer "module pointer" model, `NamespaceModuleModel` was
removed and `NamespaceSpecModel.modules` now uses the same
`ModuleReferenceModel` (common_models.py) that `TopologyComponentModel.modules`
uses. Namespace-grouped modules retroactively gain `slot_type` (validated via
the new `validate_slot_type()`/`STANDARD_SLOT_TYPES`, also newly added to
common_models.py), `enabled`, and `configuration` — a real enrichment (canary/
sidecar slots, per-namespace disabling and config overrides are now possible
for namespace-grouped modules too), not a regression.

## Consequences

- Good: a real path-traversal gap present in v1 (never checked before) is
  closed here rather than carried forward, consistent with the same fix just
  made for `ModuleFileModel`.
- Good: `validate_no_path_traversal()`'s `@reponame/` handling is now proven
  across two kinds (Module, Namespace) with identical behavior.
- Good: one shared `ModuleReferenceModel` instead of two independently-drifting
  "module pointer" shapes — caught before the second copy was ever written.
- Neutral: Namespace's `modules[].file` references remain unresolved (no
  file-existence check) until the repo-registration/work-path machinery
  exists — same trade-off already accepted for other deferred Phase 2 checks.

## Remaining Work

- `NamespaceService._validate_dynamic()` (resolving `modules[].file` against
  a real work path + repo map) is deferred until repo registration/build
  machinery exists in v2.
- Cross-layer overlap validation implied by `NamespaceType.SHARED` vs.
  `DEDICATED` (mentioned in the field's own description) **is** implemented in
  v1, but as a separate solution-wide diagnostic (`OverlapController`,
  `_check_namespace_overlap()`) — it scans *every* workspace file in a
  solution, loads each one's referenced namespaces, and warns when the same
  namespace name is claimed by manifests in different layers (unless
  `type: shared`). This is a cross-file, cross-workspace lint tool, not
  per-document Phase 1/2 validation — it needs a multi-file solution scanner
  that doesn't exist in v2 yet (no `workspace`/`solution` kind, no
  multi-manifest controller layer). Deferred until that tooling exists; not
  a schema concern for `NamespaceModel` itself.
