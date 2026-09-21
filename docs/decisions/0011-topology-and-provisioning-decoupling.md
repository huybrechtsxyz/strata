# Topology and Provisioning — Decoupling Infrastructure Grouping from Execution

- Status: partially-implemented — `Topology` promoted to its own kind;
  `ProvisionerModel`/`ProvisioningStepModel` built as Workspace sub-models
  (`provisioning_model.py`); the `Workspace` root/glue kind itself not yet
  built (see Remaining Work)
- Date: 2026-09-21
- Related: [ADR-0002](0002-requirement-interface-injection-grant-lessons-from-v1.md)
  (Grant/Injection/Context — the deploy-time concepts Provisioning will need
  to consume), [ADR-0006](0006-context-shared-stage-runtime-store.md)
  (Context — the runtime store `ProvisioningStep` output-consumption will
  eventually resolve against), [ADR-0009](0009-module-model-design-decisions.md)
  (`depends_on` precedent reused here), [ADR-0010](0010-namespace-model-design-decisions.md)

## Context and Problem Statement

Before building Workspace, v1's real `workspace_model.py` was read in full (not
just skimmed). It revealed `WorkspaceTopologyModel.provisioner` as a single
1:1 name reference to `WorkspaceIacModel` — meaning a topology could only be
built/configured by exactly one provisioner. This doesn't survive contact
with reality: a real deployment routinely has Terraform provision a cluster,
then Ansible configure only part of it, and Helm deploy workloads onto it —
three provisioners, one topology, none of them "the" provisioner for it. v1
worked around this by mixing the concepts together ("mashed together until
it worked," per this session's discussion) rather than modeling the real
shape. This ADR records a from-first-principles redesign, deliberately done
without anchoring to v1's shape, arrived at through extended Socratic
discussion (see session transcript for the full reasoning chain — summarized
below).

## The core reframing: Workspace is an image, Deployment is a container

A Workspace should be treated like a container **image**: a static,
versioned declaration of everything that *could* exist (providers, resources,
topologies, provisioners, namespaces/modules) and the *recipe* for how it's
built (which provisioner acts on what, in what order) — baked in at
authoring time by the platform engineer. A Deployment is a **running
instance** of that image: it supplies runtime parameters (which Environment,
which secrets are granted) and executes the pre-baked recipe. Critically,
**the recipe must live in the image, not be re-invented per container run** —
an operator running a deployment should not need platform-engineer-level
knowledge of which tool builds which piece of infrastructure. This directly
contradicts v1's `DeploymentStageModel.provisioner`/`.topology` fields, which
re-declare tool bindings at deploy time — identified as the same class of
mistake as putting the recipe in the wrong place.

## Three concepts, not two — and no direct link between the first two

1. **Topology** — a named grouping of resources and namespaces that
   conceptually belong together (e.g. "AKS cluster + blob store + key
   vault"). Answers *"what belongs together"* — for documentation,
   diagramming, and workload placement. **No `provider`/`provisioner`
   field.**
2. **Provisioner** — a tool (terraform/ansible/helm/...) + its source
   location + tool-specific config. Answers *"what tool does work, and
   where's its code."* Not built yet (see Remaining Work).
3. **ProvisioningStep** — the recipe entry: "run this Provisioner, targeting
   these Resources/Namespaces, after these other steps." This is the *only*
   place tool bindings and topology membership become related, and even then
   only indirectly (see below). Not built yet.

**Decision: Topology and ProvisioningStep have no direct relationship.**
Both independently reference the same underlying pool of Resources/
Namespaces. A real infra team's provisioning boundaries (state/blast-radius/
ownership splits) routinely cross topology boundaries (a shared networking
Terraform state feeding multiple topologies) or cover only part of one
topology (Ansible touching only the cluster resource, not the blob store or
key vault in the same topology) — many-to-many via a shared reference pool,
confirmed against real practice in this session's discussion, not just
theorized. "This step realizes topology X" becomes a **derived** fact
(intersect a step's `targets` with a topology's resources), never a declared
one — avoiding a redundant tag that would need to stay in sync.

## Ordering: `depends_on`, not `priority`

`ProvisioningStep`s are named, addressable entities — same shape as
`ModuleServiceModel` service entries — so `depends_on` (explicit graph, reused
verbatim from `ModuleServiceModel.depends_on`/`WorkspaceResourceModel.depends_on`/
`inputs_from`'s Kahn's-algorithm cycle detection) is the natural fit, not
`priority`. `priority` remains exactly where it already is
(`ScriptPathModel`, lifecycle hook scripts) — those are anonymous/unordered-
by-identity list entries that can't be depended on by name, so a simple
numeric tiebreaker is the only mechanism that applies there. Using both
mechanisms at the `ProvisioningStep` level would recreate the "different
strategies" problem this whole redesign is trying to avoid.

**Consequence for the "shared target, ambiguous order" case**: two
`ProvisioningStep`s targeting the same Resource/Namespace **must** have a
`depends_on` edge (direct or transitive) between them — hard validation
error, not a warning, since undefined ordering on a shared infrastructure
target is a real correctness/safety issue (state corruption, race
conditions), not just a labeling collision like `NamespaceType`'s overlap
warning.

## Decision: `Topology` is promoted to its own top-level kind

Rather than nesting `WorkspaceTopologyModel` inline inside Workspace (v1's
approach), `Topology` becomes a standalone kind (`TopologyModel`,
`TopologyService`, `PlatformKind.TOPOLOGY`) — consistent with every other
kind Workspace references (`Namespace`, `Firewall`, `Dns`, `Network` are all
standalone, referenced by name+file pointer, not embedded). Workspace will
reference topologies the same way once built. This also fits the
image/container framing: a topology (a reusable "this is what a Kubernetes
platform looks like" shape) is exactly the kind of thing a platform engineer
would want to define once and reference from multiple workspaces, the same
way a Provider or Resource definition is reused.

`TopologySpecModel` fields: `type` (label, e.g. kubernetes/dockerswarm/
azure-native), `components` (resource name references, min 1), `namespaces`
(namespace name references), `volumes` (ported from v1's
`WorkspaceVolumeModel` unchanged). Unique-reference validators for all three
lists, all self-contained (Phase 1).

### `components[].resource`/`namespaces[].namespace` — syntax-checked, not existence-checked

Promoting `Topology` to a standalone kind has a direct cost: in v1,
`WorkspaceTopologyModel` was nested inside `WorkspaceModel`, so cross-checking
`components[].resource` against `workspace.spec.resources[].name`
(`validate_topology_resource_references`) was a Phase 1, same-document model
validator — free. Now that `Topology` is its own file, that document has no
visibility into what resources exist anywhere; existence-checking becomes a
necessarily cross-document Phase 2 concern, and the only place it can live is
**Workspace**, once built (`WorkspaceService._validate_dynamic()`, loading the
referenced `Topology` document and cross-checking against its own
`spec.resources[]` — same check v1 did, just moved down a phase because the
two pieces of data no longer live in one file). `Topology.spec` itself will
never be able to validate this alone.

As a partial, free mitigation, `resource`/`namespace` were tightened from
plain `str` to `PlatformName` (v1 used plain `str` here) — catches
syntactically malformed names (e.g. `AksCluster`, `My App`) at Phase 1, even
though it can't confirm the name actually exists.

### `TopologyComponentModel.modules` — attaching a module directly to a resource

A resource within a topology can need application code attached directly
(e.g. an Azure Function App's function code onto its Function App resource)
— no container-orchestration namespace involved (contrast with
`Topology.spec.namespaces`, for the k8s/compose-style grouping case).
v1 modeled this via `WorkspaceModuleReferenceModel` on
`WorkspaceResourceModel` (Workspace's resource-override layer), with fields
`name`/`file`/`slot_type`/`enabled`/`configuration`.

Before adding this, checked how it related to the already-built
`NamespaceModuleModel` (`name`/`description`/`file`) to avoid duplicating a
second "module pointer" shape. Both are ultimately the same thing — a
pointer to a `Module` document plus placement metadata — differing only
because v1 happened to add `slot_type`/`enabled`/`configuration` where it
first needed them (the resource-attachment case), not because
namespace-grouped modules have less legitimate need for them (disabling a
service, per-namespace config overrides, and canary/sidecar slots are
equally applicable there). Resolution: **one shared `ModuleReferenceModel`**
(common_models.py: `name`, `file` [path-traversal-protected, reusing
`validate_file_ref_no_traversal`], `description`, `slot_type` [validated via
the new `validate_slot_type()`/`STANDARD_SLOT_TYPES`], `enabled`,
`configuration`), used by both `NamespaceSpecModel.modules` and
`TopologyComponentModel.modules`. `NamespaceModuleModel` was removed
entirely rather than kept as a near-duplicate (see ADR-0010 Decision 6).

Ported alongside it: v1's "exactly one `main` slot among enabled modules"
rule (`WorkspaceSpecModel`'s resource-module-slot validator), scoped per
`TopologyComponentModel` here (each resource's attached modules must have
exactly one enabled `main` slot if more than one module is attached) — same
rule, just relocated to where the modules now live.

### `ProvisionerType`/`ServiceDeployerType` unified (groundwork for `Provisioner`)

Before building `Provisioner`, revisited an open question from earlier in
this ADR's discussion: v1 has `ServiceDeployerType` (helm/compose/argocd/
script, `Module.spec.type`) and a separate `ProvisionerType` (adds terraform/
ansible/bicep/flux, workspace/topology-level), never reconciled — the same
class of duplication as `ModuleReferenceModel`/`NamespaceModuleModel` above,
just at the type-vocabulary level.

**Decision: one canonical `ProvisionerType` enum** (8 members: terraform,
ansible, bicep, script, helm, compose, argocd, flux), reused by both
`Module.spec.type` and the future `Provisioner.tool`. Subsets that mean
something different in different contexts are named, documented `frozenset`
constants, not separate enum classes:

- `SYNC_PROVISIONER_TYPES` (argocd, flux) — intrinsic tool property: renders
  from the platform artifact, no IaC source directory needed.
- `WORKLOAD_DEPLOYER_TYPES` (helm, compose, argocd, script) — intrinsic tool
  property: can deploy a Module's containers/sub-charts. Replaces
  `ServiceDeployerType`'s vocabulary exactly; `ModuleSpecModel` gained a
  field validator rejecting terraform/ansible/bicep here.

**Deliberately not built**: a hardcoded "integration-aware" subset
(terraform/ansible/bicep, per v1's `WorkspaceIacModel.integration` gating,
ADR-0079/0080). Unlike the two sets above — which are facts about how a
tool intrinsically works — "which provisioners can bind to an Integration"
is a capability the not-yet-built `Integration` kind should declare itself,
the same way Provider/Resource type-checking goes against `Configuration`'s
real registry rather than a Python constant. `Provisioner.integration` (once
built) will be an unchecked `str | None` reference until `Integration`
exists to give it meaning — same discipline as `output_key`/Context
(ADR-0006).

**Corrected immediately after**: `Module.spec.type` was initially typed as
the closed `ProvisionerType` enum. Checking v1's real extensibility
(`strata/deployers/factory.py`'s `DeployerFactory`) found built-in tools
**and** user-registered provisioner plugins (`.strata/provisioners/*.py`,
optionally with a `provisioner.yaml` manifest) tracked in one runtime
registry (`DeployerFactory.is_known_type()`) — a real, working extensibility
point, not hypothetical. A closed enum would reject a legitimately-supported
custom provisioner name. Fixed: `type` is now `PlatformName` (an open
string), matching `Provider.type`/`Topology.type`'s existing open-string-
plus-registry pattern. `ProvisionerType` remains the reference set of known
built-ins; the validator only rejects values that **match a known built-in**
and aren't a workload deployer (terraform/ansible/bicep) — an unrecognized
value (a possible custom plugin) passes through unchecked, deferred to
Phase 2 once a plugin registry exists (see ADR-0009 Decision 9 for the full
write-up).

### `ProvisionerModel`/`ProvisioningStepModel` built (`provisioning_model.py`)

Both are Workspace **sub-models**, not standalone kinds — no
`PlatformKind`/`apiVersion` wrapper, no dedicated service, same shape as
`SourceModel`/`AuthenticationModel`.

`ProvisionerModel`: `name`, `description`, `tool` (open `PlatformName` string
— same reasoning as `Module.spec.type`), `source`, `backend`
(`ProvisionerBackendModel`), `properties` (`ProvisionerAnsiblePropertiesModel`),
`configuration` (freeform passthrough), `version`.

**Correction #1 (2026-09-21, after reviewing v1's real behavior in detail)**:
initially gave `source`/`backend`/`properties` a `Module.spec.type`-style
"unrecognized tool → don't reject" exception. Re-checking v1's actual
`WorkspaceIacModel.validate_provisioner_fields()` found it does the
opposite: unconditional string/enum equality, with **no exception for
custom/unrecognized provisioner names** — `is_sync = self.provisioner in
{...}`, `self.provisioner != ProvisionerType.ANSIBLE`, etc. Critically, the
comment above the `backend`/`output` check cites a real incident: *"previously
validated successfully and was then silently ignored everywhere; fail loudly
instead"* (v1 ADR-0071) — the exact dead-field bug class this whole v2
rewrite has been hunting down elsewhere (Provider's `custom`/`default_tags`,
Auth's `env_vars`). Digging further into `IntegrationService.resolve_for_provisioner()`
confirmed v1's *only* real leniency (OpenTofu treated as terraform-compatible)
lives in a **runtime Integration-class hierarchy** (`OpenTofuIntegration`
subclasses `TerraformIntegration`), not in the Pydantic schema layer at all —
the schema was always meant to be strict. Reverted, at that point, to match
v1's unconditional strictness exactly.

**Correction #2 (2026-09-21, immediately after — restoring the original
leniency)**: Correction #1 over-applied v1's strictness. There are two
genuinely different questions: *"adding a third-party/custom provisioner
plugin"* (no strata core change — `.strata/provisioners/*.py`) vs.
*"shipping official, first-party strata support for a brand-new tool"*
(inherently a code change, same as v1's `DeployerFactory._BUILTIN_MAP`
needing a literal entry per built-in). v1's unconditional string comparison
conflates these — it was never a deliberate "block custom plugins from
`backend`/`properties`" design decision, just an artifact of simple code
that never reasoned about the custom-plugin case at all. For a genuinely
unrecognized `tool`, the plugin's *own* deployer code is the only consumer
of `ProvisionerModel` — there's no "known correct" tool to compare against,
so rejecting `backend`/`properties`/omitted-`source` there protects against
nothing real; it just forces plugin authors into the untyped `configuration`
dict for no reason, and assumes every custom tool needs a `source` when a
custom plugin could just as legitimately be sync-like by its own design.

**Final resolution**: reject only when `tool` **is** a recognized built-in
that's *known* to be wrong (the real ADR-0071 protection, e.g. `backend` set
with `tool: ansible`); allow through when `tool` doesn't match any
recognized built-in (a possible custom plugin — its own code decides).
`SYNC_PROVISIONER_TYPES`/`TERRAFORM_COMPATIBLE_TYPES` still gate the
*recognized* cases exactly as before. This means: **adding a new custom
provisioner never requires a schema change** — it already works today via
the open `tool` string plus this leniency. A schema change (adding to
`ProvisionerType`/`TERRAFORM_COMPATIBLE_TYPES`) is only needed when
*officially* elevating a tool to first-party built-in status, which is
inherently bundled with writing that tool's actual builder/deployer code —
not a separate, artificial barrier.

`ProvisioningStepModel`: `name`, `provisioner` (name reference, existence
deferred to Workspace), `targets` (Resource/Namespace name references, min
1, unique), `depends_on` (explicit graph, no self-reference). Deliberately
has **no** `priority` field (ADR-0011's own earlier conclusion) and **no**
topology reference (Topology/Provisioning stay decoupled via the shared
Resource/Namespace pool, per this ADR's core reframing).

`validate_provisioning_steps()`: a plain function (not a `model_validator`,
since it needs sibling awareness across the whole list — no single step or
Workspace model exists yet to own this), ready to be called once Workspace
holds `list[ProvisioningStepModel]`. Validates: unique step names, every
`depends_on` name resolves to a real step, no cycles (Kahn's algorithm,
same technique as v1's `validate_inputs_from`), and the hard error this
whole ADR's design was building toward — two steps sharing a target must
have a `depends_on` edge (direct **or transitive**) between them.

**Deliberately deferred, not built now** (each has a real reason, not just
"ran out of time"):

- **`output` profile** (v1's `OutputProfileModel`/`EmitCategory`/`OutputFileModel`
  — which tfvars categories a terraform provisioner emits). Deeply
  build-layer-specific; no builder exists in v2 to consume it yet.
- **`inputs_from`** (v1's `ProvisionerInputMappingModel` — mapping one
  provisioner's outputs to another's input variable names). Its *ordering*
  half is now `ProvisioningStep.depends_on`; its *data-mapping* half (which
  output name becomes which input variable) requires a real Context
  (ADR-0006) to have any resolvable meaning — deferred until Context exists,
  not modeled as an inert field now.
- **`integration`** (v1's provisioner-to-Integration binding, ADR-0079/0080).
  Already decided against building a hardcoded "integration-aware" subset
  (see above) — the field itself is equally deferred, for the same reason:
  no `Integration` kind exists yet to give it meaning.
- **A `needs`-shaped "required environment keys" list** (v1's
  `ProvisionerReferencesModel`, real ADR-0078 precedent — not the rejected
  Requirement pattern, but the actual `needs ∩ Environment` fallback
  ADR-0002's Injection formula already anticipated for provisioners with no
  parseable Interface). Deferred until the `Environment` kind and the
  Interface/Injection build layer exist — adding it now would be a schema
  field with nothing real to check it against.

## Consequences

- Good: the 1:1 `topology↔provisioner` cramping that motivated this whole
  redesign is gone — provisioning boundaries can now match real blast-radius/
  ownership splits instead of being forced to align with topology grouping.
- Good: an operator deploying doesn't need tool-level knowledge — that
  knowledge lives in the Workspace's `ProvisioningStep` recipe, authored once
  by the platform engineer.
- Good: `Topology` is independently authorable/reusable, consistent with
  every other kind Workspace will reference.
- Good: resource-attached modules (PaaS-style, e.g. Function App code) are
  expressible directly on `TopologyComponentModel`, using the same
  `ModuleReferenceModel` as namespace-grouped modules — one shape, two
  attachment points, not two shapes.
- Good: `ProvisionerModel`/`ProvisioningStepModel` are fully self-contained
  and testable in isolation (24 tests) before Workspace itself exists to
  wire them together — same incremental pattern as every other sub-model.
- Neutral: `Topology.spec.components[].resource`/`namespaces[].namespace`
  cannot be validated against real `Resource`/`Namespace` documents yet — no
  solution-wide loading machinery exists (same deferral already accepted for
  `Namespace.spec.modules[].file`, ADR-0010). Existence-checking is Workspace's
  Phase 2 job (see above), not something `Topology` will ever do alone.
- Neutral: four real v1 concepts (`output` profile, `inputs_from` data-mapping,
  `integration`, provisioner `needs`) are deliberately absent from
  `ProvisionerModel` for now — each requires a layer that doesn't exist yet
  (build/output, Context, Integration, Environment/Interface respectively).

## Remaining Work

- ~~Build the actual `Workspace` root/glue model~~ — done, see
  [ADR-0012](0012-workspace-model-design-decisions.md).
- Extend `ConfigurationModel` with `topologies`/`ConfigurationTopologyModel`
  (mirrors the existing `providers` registry pattern) so `Topology.spec.type`
  can be cross-checked in Phase 2, same pattern as Provider's `type`/`region`.
- `TopologyService._validate_dynamic()` (checking `components`/`namespaces`
  references against real documents) deferred until solution-wide loading
  exists.
- The `Deployment` kind (the "container instance") — references a Workspace
  + an Environment, executes the baked-in `ProvisioningStep` recipe via thin
  `DeploymentStage` entries (approval gates, secrets scope/Grant) — not
  started. `DeploymentStageModel.provisioner`/`.topology` must NOT be ported
  from v1; that responsibility belongs entirely to Workspace's recipe.
- Cross-repo reference convention inconsistency noted but not resolved: v2
  currently has two syntaxes for "this lives in another repo" —
  `SourceModel.repository` (name field + separate path fields) vs. `@reponame/path`
  (embedded in a string, used by `ModuleFileModel`/`NamespaceModuleModel`).
  Revisit when building `Provisioner.source` (which will need one of these).
