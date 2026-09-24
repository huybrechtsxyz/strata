# `strata build run` — Rendering Artifacts from Integrations

- Status: proposed - design written, not implemented
- Date: 2026-09-23
- Related: [ADR-0021](0021-integration-layer.md) (Phases 1-6, all done - the
  integration layer this consumes: registry, `Integration`/`InfraIntegration`,
  `TerraformIntegration`/`ComposeIntegration`/`HelmIntegration`),
  [ADR-0018](0018-source-model-unified-remote-reference.md) (`SourceModel`,
  reused unchanged for materialising `provisioner.source`),
  [ADR-0023](0023-build-output-rendering.md) (split out of this document -
  what `prepare()`/`prepare_namespace()` actually write, and how a user can
  customise it)

## Context and Problem Statement

ADR-0021 built the integration layer up through its first three real
`InfraIntegration` classes (Terraform - Phase 5; Compose and Helm - Phase 6),
deliberately stopping short of anything that calls them. Quoting that ADR's
own Phase 7 placeholder, which this document replaces:

> The first actual consumer: wires provisioner -> integration resolution
> (including D4's auto-bind-or-error) into artifact rendering. Large enough
> to deserve its own ADR rather than being specified here.

`strata build run` is proven, critical-path v1 functionality (per
`/memories/repo/v1-consumer-usage.md`'s Tier 1 census: renders **both**
Terraform and Helm artifacts, depended on by every real haven/cfg-int-deployment
workflow).

**v1's real pipeline is much larger than this document builds.**
`RunBuildCommand` orchestrates 7 builders (`PlatformBuilder`, `TerraformBuilder`,
`AnsibleBuilder`, `BicepBuilder`, `ComposeBuilder`, `HelmBuilder`, `SbomBuilder`,
`SyncBuilder`) plus lock-mode checks, audit, SBOM scanning, an `--ai` flag, and
cache warming. Cross-checked against real usage: only Terraform and Helm are
Tier 1 (confirmed critical path); Ansible is explicitly Tier 2 ("not proven
depended upon"); SBOM/audit/`--ai`/lock-mode have no confirmed `build run`
consumer in either real repo. This ADR scopes to what's proven, same
discipline as every ADR-0021 phase.

**`PlatformBuilder` (v1's intermediate `platform.json` snapshot) is
deliberately not ported.** It exists in v1 for two reasons: (1) handing
resolved state to the next builder in-process, and (2) `collect_platform_artifact()`
hashes it into the deployment manifest for provenance. (1) is unnecessary in
v2 - the solution is already fully loaded and resolved in memory
(`SolutionContext`, `resolve_deployment_chains()`, `merge_environment_models()`,
`resolve_values()`); there is nothing to hand off across a process boundary
within one `build run` invocation. (2) has no v2 consumer: the deployment
manifest itself is explicitly deferred (`ConfigurationSpecModel`'s own
docstring lists `deployment.manifest/outputs` as not yet modelled). Checked
against a real haven workflow before cutting this: `build run` and
`deploy run` DO round-trip `build/` through `actions/upload-artifact`/
`download-artifact` even within one job - but that's the *rendered output
directory* (`.tf`/`.tfvars.json`/`values.yaml`), never in question either
way. `platform.json` specifically has no reason to exist without the
manifest feature that reads it. Revisit if/when that feature is designed -
it can hash the rendered output directly, which is cheaper than reinventing
a snapshot format.

## Decision

**One orchestrator loop, zero tool-specific branching.** The loop below is
the entire `build run` logic - it never inspects `provisioner.tool` beyond
passing the string to `registry.get()`:

```python
def build_run(context: SolutionContext, deployment_name: str, build_path: Path) -> Diagnostics:
    deployment = resolve_deployment(...)                          # existing (Phase 2-era controller)
    resolved = resolve_values(context, deployment_name, keys=...)  # existing (ADR-0021 Phase 4)
    workspace = ...                                               # deployment -> workspace, existing

    for step in ordered_by_depends_on(workspace.spec.execution):   # existing model, topological sort
        provisioner = find_provisioner(workspace, step.provisioner)
        integration = resolve_integration(context.controller.index, provisioner)  # D4, below
        source_path = sync_source(provisioner.source, build_path / step.name)     # ADR-0018, below
        integration.prepare(source_path, resolved=resolved, provisioner=provisioner)
    return diagnostics
```

**D1: `InfraIntegration` gains a fourth method, `prepare()`.** Rendering a
build-time artifact (`.tfvars.json`, Helm `values.yaml`, an Ansible
extra-vars file, a Bicep parameters file) is the same kind of format-specific
work Phase 5/6 already put *inside* each Integration class (Terraform builds
its own `-var-file` argv; Helm builds its own `-f`/`--set-string` argv).
Rendering follows the same rule, one level earlier:

```python
class InfraIntegration(Integration):
    @abstractmethod
    def prepare(self, path: Path, *, resolved: ResolvedValues, provisioner: ProvisionerModel, **kwargs: Any) -> Path:
        """Render whatever this tool needs into `path` from already-resolved
        values and this provisioner's own typed config. Returns the path
        `plan`/`deploy`/`destroy` should be called against.

        What gets written, and how a user can override it, is ADR-0023 -
        not specified here; this ADR only fixes the method's existence and
        signature, which is what the orchestrator loop above depends on.
        """
```

`resolved: ResolvedValues` is exactly what `value_controller.resolve_values()`
already produces (Phase 4) - no new resolution logic. `provisioner:
ProvisionerModel` is handed down **whole**, never picked apart by the
orchestrator: `TerraformIntegration.prepare()` reads `provisioner.backend`
itself; a future `AnsibleIntegration.prepare()` would read
`provisioner.properties` itself. The orchestrator does not know or care
which tool it is holding.

**D2: `resolve_integration()` (D4's auto-bind-or-error) reads only strings,
never a tool identity.**

```python
def resolve_integration(index: DocumentIndex, provisioner: ProvisionerModel) -> Integration:
    if provisioner.integration:
        doc = index.get(PlatformKind.INTEGRATION, provisioner.integration)  # existence already validated
        return registry.get(doc.model.spec.type, config=doc.model)
    candidates = [e for e in index.all_of(PlatformKind.INTEGRATION)
                  if e.model.spec.enabled and e.model.spec.type == provisioner.tool]
    if len(candidates) > 1:
        raise UsageError(f"Provisioner '{provisioner.name}': multiple '{provisioner.tool}' integrations declared "
                          f"({[c.model.meta.name for c in candidates]}) - set 'integration:' explicitly.")
    config = candidates[0].model if candidates else None
    return registry.get(provisioner.tool, config=config)   # falls back to env/PATH-only, today's default
```

Named-binding wins outright when set. Otherwise: zero or one matching
enabled Integration document auto-binds (zero falls back to the same
env/PATH-only construction Phase 4/5/6 already default to); more than one is
an error naming every candidate, never a guess.

**D3: `sync_source()` is not new design - it is a caller of already-built
machinery, with one real gap found while checking cross-repo evidence.**
`ProvisionerModel.source: SourceModel` already models git/OCI/local
uniformly (ADR-0018). "Materialise `source` into `build_path/<step>/`" is
the same operation regardless of what's inside (Terraform `.tf`, an Ansible
playbook, Bicep templates, a Helm chart directory). **The gap**: a real
workspace (`cfg-int-deployment`'s `stacks/spoke/workspace.yaml`) declares a
second, "copy-only" provisioner (`core_modules`) that is never planned or
deployed - it exists solely to stage a shared Terraform module library at a
fixed relative path, because the real provisioner's `.tf` code composes it
via a relative `source = "../../core/terraform/components/aks"`. `build_run`'s
per-step materialisation (D1's loop places each step at `build_path/<step>/`
independently) must therefore preserve the *relative* directory layout
between sibling provisioners' outputs, not just get each one individually
right - a provisioner that's never in `execution` still needs its source
materialised if another provisioner's `.tf` composes it by relative path.

**D4: `build run` renders; it does not execute.** Matches v1's real split
exactly (`build run` produces files; `deploy run` runs `plan`/`apply`). This
design calls only `prepare()` - never `plan`/`deploy`/`destroy`.

**What `prepare()` actually writes, and how a user can override it, is
[ADR-0023](0023-build-output-rendering.md), not here.** That document
covers: the real structural shape of a rendered artifact (a full projection
of the resolved platform graph, not just declared variables), why
`OutputProfileModel` is not ported (checked against real usage and found
unused), the Jinja2 template escape hatch that *is* kept (and why usage
evidence alone couldn't settle that question - it answers strata's
reversibility promise, not a feature-usage count), and the worked Terraform/
Ansible/Bicep walkthroughs. This ADR's orchestrator loop only needs
`prepare()` to exist with the signature above - it never needs to know what
ends up inside `path`.

## D5-D7: a second, independent pipeline - the workload path (Compose/Helm)

**Found while checking "how did v1 shape the input" before writing this
section - the orchestrator loop above (D1-D4) does not cover Compose/Helm at
all.** v1's real `ComposeBuilder`/`HelmBuilder` are driven by
`Namespace.spec.modules`, never by `ProvisionerModel`/`ProvisioningStepModel`.
This is a second, disconnected input shape, not a variant of the first one -
confirmed by reading `ComposeBuilder`/`HelmBuilder` directly rather than
assuming the provisioner-step model covers everything `InfraIntegration`
serves.

**D5: resolution is a bare type string, not D2's auto-bind.** A Module has
no `integration:` binding field in v1 - `module.spec.type` (`"compose"`,
`"helm"`, `"argocd"`, ...) is looked up directly:

```python
integration = registry.get(module.spec.type)   # no ProvisionerModel involved at all
```

If a real need for named Integration binding on a Module appears later
(pinning a specific Helm binary/transport per module, say), it attaches the
same way D2 does for provisioners - not designed here, no evidence for it yet.

**D6: the workload loop groups and merges differently per `type` - this is
real per-tool variation the orchestrator must still not encode.**

```python
def build_workload_modules(namespace: NamespaceModel, resolved: ResolvedValues, build_path: Path) -> None:
    modules = [resolve_module(ref) for ref in namespace.spec.modules or []]
    by_type: dict[str, list[ModuleModel]] = group_by(modules, key=lambda m: m.spec.type)
    for module_type, group in by_type.items():
        integration = registry.get(module_type)
        integration.prepare_namespace(namespace, group, resolved=resolved, build_path=build_path)
```

`prepare_namespace()` (not `prepare()` - deliberately a different method,
see D7) is where the real per-tool shape difference lives, entirely inside
each class:

- **Compose**: merges every module in `group` into **one**
  `{build_path}/{namespace}/docker-compose.yml` (services prefixed
  `{module}-{service}`, collision-free by construction). A module may set
  `spec.compose_file` to opt out of generation and copy a file verbatim
  instead - mutually exclusive with `spec.services`, at most one per
  namespace.
- **Helm**: never merges - writes **one `values.yaml` + one `meta.yaml` per
  module**, at `{build_path}/{namespace}/{module}/`.

Neither behaviour is visible to `build_workload_modules()` - it hands the
whole group to whichever class `registry.get(module_type)` returned and
does not know if that class merges or not.

**D7: `prepare_namespace()`, not `prepare()` - workload integrations need a
namespace-scoped grouping signature `InfraIntegration.prepare()` doesn't
have.** `prepare(path, *, resolved, provisioner, **kwargs)` (D1) is shaped
around one Provisioner producing one output tree. The workload path needs
"here is every same-type module in this namespace, together" (Compose's
whole reason for existing is the *merge*). Rather than force-fitting one
signature over both, `ComposeIntegration`/`HelmIntegration` gain a second
method - `prepare_namespace(namespace: NamespaceModel, modules: list[ModuleModel],
*, resolved: ResolvedValues, build_path: Path) -> None` - alongside
`prepare()`, not replacing it: `TerraformIntegration` never implements
`prepare_namespace()` (nothing to group), `ComposeIntegration`/
`HelmIntegration` implement both (`prepare()` stays available for a lone
module, `prepare_namespace()` for the real per-namespace grouping/merge
behaviour). Whether this belongs on `InfraIntegration` itself (as a second
abstract method every class must at least trivially implement) or a
separate mixin ABC only Compose/Helm opt into is an open call for whoever
implements this - not resolved here, since nothing forces the choice yet
(only two classes need it, both container-capability).

**What `prepare_namespace()` actually writes per tool (the merge/no-merge
behaviour above, value-substitution token shapes, deploy-time resolution)
is also [ADR-0023](0023-build-output-rendering.md)'s scope, not this
document's** - covered there alongside the equivalent Terraform detail, for
the same reason: this ADR fixes the method's existence and grouping
behaviour; what ends up in the rendered file is a separate design.

## Consequences

- Good: adding a tool (Ansible, Bicep, OpenTofu, ...) never touches the
  orchestrator - only a new `Integration` subclass and a `_KNOWN` entry,
  proven twice already (Phase 5 -> Phase 6).
- Good: no intermediate snapshot format to design, version, or keep in sync
  with the document models it summarises.
- Good: secrets are never written to disk during `build run` - `prepare()`
  only ever sees `resolved.variables`/`resolved.features`, never
  `resolved.secrets`; matches v1's own stated security rule for
  `TerraformBuilder` and Phase 5/6's `env`-not-argv secret handling.
- Bad: no deployment-manifest provenance hash exists yet - deferred, along
  with the feature that would consume it. `build/`'s round-trip through CI
  artifact storage (confirmed real, via haven's workflow) still works without
  it; only the *hash-embedded-in-a-manifest* part is missing until that
  feature is designed.
- Neutral: disabled-resource/module filtering (`WorkspaceResourceModel.enabled`
  exists) is not addressed by this design - first pass assumes everything in
  `workspace.spec.resources`/`execution` is enabled. Revisit if validation or
  a real solution surfaces the gap.
- Neutral: two independent pipelines (D1-D4 provisioner-driven,
  D5-D7 module-driven), not one - a real cost in surface area, but matches
  v1's actual architecture rather than forcing a false unification.
  `TerraformIntegration` never implements `prepare_namespace()`;
  `ComposeIntegration`/`HelmIntegration` never go through
  `resolve_integration()`'s D2 auto-bind. Neither loop branches on tool
  identity internally, which is the property that actually mattered.
- Neutral: what `prepare()`/`prepare_namespace()` write, and whether/how a
  user can override it, is [ADR-0023](0023-build-output-rendering.md) - not
  a small design surface, but this ADR only needs the two methods to exist
  with the signatures above; it does not need to resolve what ends up
  inside `path`.

## Remaining Work

- Implementation: `strata/controllers/build_controller.py` (the orchestrator
  loop), `resolve_integration()` (D2), `sync_source()` (D3, including the
  sibling-provisioner relative-path composition gap), `prepare()`/
  `prepare_namespace()` existing on `InfraIntegration` (D1/D7) and each of
  `TerraformIntegration`/`ComposeIntegration`/`HelmIntegration` (their
  bodies are ADR-0023's scope, not this one's).
- The workload pipeline (D5-D7): `build_workload_modules()`, the
  namespace/module grouping-by-type logic (D6), and deciding where
  `prepare_namespace()` formally lives (D7's open call - `InfraIntegration`
  itself vs. a separate mixin ABC).
- `strata build run` CLI command wiring (`strata/commands/build_command.py`),
  matching `validate_command.py`'s established thin-glue-over-controller shape.
- Everything about what gets rendered/how it can be customised -
  `build_platform_projection()`/`planned_files()`, `resolve_expr_tokens()`,
  `OutputModel`/`output.template`, shipped example templates, Compose/Helm's
  own deploy-time value substitution - is tracked in
  [ADR-0023](0023-build-output-rendering.md)'s own Remaining Work, not
  duplicated here.
- Helm's OCI `chart_repository` support and the "must not Jinja-render a
  local chart's own `templates/` dir" rule - both real v1 1.8.2 bug fixes
  (ADR-0020), requirements not discoveries to make again.
- `TF_VAR_`/compose env injection at *deploy* time (v1's
  `resolved_values.as_tf_vars()`/`as_compose_env()`) - out of scope for
  `build run` (D4); belongs to `deploy run`'s own design.

