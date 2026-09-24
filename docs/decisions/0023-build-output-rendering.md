# Build Output Rendering — Default Projection and a Jinja2 Escape Hatch

- Status: proposed - Phase 1, 2a, and 2c's dns/networks implemented;
  2b (modules) skipped, 2c's tenant / Phases 3/4/5 not yet
- Date: 2026-09-24
- Related: [ADR-0022](0022-strata-build-run.md) (`strata build run` - the
  consumer: `InfraIntegration.prepare()`/`prepare_namespace()` call into this
  design to produce whatever they write to disk), [ADR-0021](0021-integration-layer.md)
  (the `InfraIntegration` ABC this document's rendering logic sits behind)
- See also: [docs/design/build-pipeline-status.md](../design/build-pipeline-status.md)
  for cross-ADR status across ADR-0021/0022/0023

## Context and Problem Statement

Split out of ADR-0022, which originally specified this inline until it grew
large enough (and was revised enough times mid-design) to deserve its own
document - the same reasoning ADR-0022 itself was split out of ADR-0021 for.
ADR-0022's orchestration (`prepare()`/`prepare_namespace()` exist, are called
once per provisioner/namespace, never branch on tool) is settled and stays
there. What each of those methods actually **writes** - v1's real tfvars
shape, whether/how a user can customise it, and what mechanism does the
customising - is this document.

Two things forced this to be revised multiple times before it stabilised,
both worth restating since they're easy to re-litigate by accident:

1. **v1's real tfvars are a full structural projection of the resolved
   platform graph** (every Resource/Network/Firewall/DNS zone/Topology/
   Module/Tenant document, flattened into typed nested payloads), not just
   environment-declared variables - an earlier draft assumed the simpler,
   wrong thing.
2. **Customisation is real, but not for the reason "check real usage"
   first suggested.** Checked against all three real workspaces available
   (haven's single-repo terraform provisioner, and `cfg-deployment`'s
   two workspaces referencing a genuinely separate dedicated Terraform repo,
   `iac-deployment`) and found **zero** use of v1's `OutputProfileModel`
   (`format`/`emits`/`files`/`script`). That's real evidence the *emit-
   suppression* mechanism is over-built, same pattern as Ring/Promotion and
   `pins.tools` elsewhere in this project. But both real repos were authored
   *by the same person who designed strata*, with `.tf` code written from
   day one to consume strata's own category names - neither can demonstrate
   whether someone layering strata over **pre-existing, foreign
   infrastructure code** needs something the default projection can't give
   them, because neither ever had foreign code to reconcile with. That
   question matters because of strata's own stated promise: *"you can layer
   strata over your deployments, and take it away again."*
   `cfg-deployment` already violated that promise once - product
   Terraform had to move into a separate `iac-deployment` repo instead
   of living next to the application code, which was never the intention.
   The default projection is part of why: it requires `.tf` code to either
   declare strata's own vocabulary (coupling it to strata) or tolerate a
   pile of unused `*.auto.tfvars.json` files it never asked for - neither is
   "layer on, then delete strata and nothing changes."

## Decision

**D1: the real shape is a full structural projection, one payload per
document category.** v1's `.tf` code consumes typed variables like
`var.resources_by_category`/`var.networks`/`var.namespaces` - every
Resource/Network/Firewall/DNS zone/Topology/Module/Tenant document in the
resolved workspace graph gets flattened into one matching payload:

```python
{
    "workspace": ..., "providers": ..., "topologies": ...,
    "resources_by_category": ..., "modules": ..., "namespaces": ...,
    "firewalls": ..., "dns": ..., "networks": ..., "tenant": ...,
    "required_variables": ..., "required_features": ..., "required_secrets": ...,
}
```

This is the real mechanism binding strata's declarative documents to
Terraform's variable declarations - not a side detail `prepare()` can skip.
Each payload maps to its own `*.auto.tfvars.json` file (Terraform's
auto-loading convention - no `-var-file` flag needed): `workspace.auto.tfvars.json`,
`providers.auto.tfvars.json`, `resx_<type>.auto.tfvars.json` per resource
category, etc. `required_variables`/`required_features`/`required_secrets`
are a separate **requirements manifest** (which keys were referenced, by
what, not their values) - documentation, not data a `.tf` file consumes.
Empty-payload categories are skipped automatically - free, not a
configuration knob (see D2).

**D2: output shaping is default-only for the built-in categories -
`OutputProfileModel` is not ported.** Terraform tolerates undeclared
`.tfvars` keys as a warning, not an error - a foreign `.tf` repo that only
declares `variable "resources_by_category" {}` and nothing else simply
ignores every other emitted category file, so D1's projection is naturally
safe to over-emit without a suppression mechanism. The real cross-repo-
variation mechanism that *is* kept: `${var:KEY}`/`${secret:KEY}`/
`${feature:KEY}` typed-expression substitution (v1 ADR-0075) inside a
provisioner's own `configuration`/`backend` dicts - confirmed directly in
`cfg-deployment`'s real `ProvisionerBackendModel.configuration`:

```yaml
backend:
  type: azurerm
  configuration:
    resource_group_name: ${var:tf_state_resource_group}
    key: int-${var:spoke}-${var:customer_code}-${var:environment}.tfstate
```

This is the real answer to "different repos need different specific
inputs" - substitution *within* whatever config a provisioner already
declares, not a separate emit-suppression layer. `prepare()` must resolve
these tokens against `resolved` wherever they appear in
`provisioner.backend`/`.configuration`/`.properties` - not designed in full
here (see Remaining Work), but confirmed necessary, unlike
`OutputProfileModel`. Revisit `OutputProfileModel` only if a real `.tf` repo
shows up needing a variable name/shape D1's projection genuinely cannot
produce - not "might produce a warning", an actual blocker.

**D3: a user-authored Jinja2 template is the real escape hatch for the
reversibility case D2's evidence can't rule on.** The mechanism: a template
file the user writes themselves, committed alongside their own `.tf` code
(or Ansible/Bicep/anything else), rendered against strata's resolved
context:

```yaml
provisioners:
  - name: app_iac
    provisioner: terraform
    source:
      repository: myapp
      source_path: infra/terraform        # .tf code lives next to the app, not in a separate iac repo
    output:
      template: infra/terraform/strata.tfvars.json.j2   # theirs, committed next to the .tf code
```

**Not a new templating system - the existing shared one
(`strata/utils/templater.py`), not raw ad hoc Jinja2.** That module already
has exactly one hardened Jinja2 setup reused across scaffolding *and*
builders (and the diagram-render filters live in the same module) - two
environments, not one: `_STRICT_ENV` (`StrictUndefined` - a missing variable
raises) for env-var-file substitution, `_LENIENT_ENV` (`DebugUndefined` - a
missing variable stays visible as literal `{{ var }}`) for scaffold
templates, where a human edits the result next. **Output templates use
strict semantics, not lenient** - a scaffold file left with a visible
`{{ var }}` is fine (a human looks at it next); a `.tfvars.json.j2` silently
rendering to a literal `"{{ var }}"` string is broken Terraform, discovered
downstream at `plan`/`apply`, far from the actual mistake. Not sandboxed
(plain `jinja2.Environment`, not `SandboxedEnvironment`) is also correct
here: the template is the user's own committed file in their own repo - the
same trust boundary as the `.tf` code sitting right next to it, not
third-party/untrusted input. `autoescape=False` carries over unchanged -
output is code/config, never HTML. Building JSON output safely inside a
Jinja2 template means using the `tojson` filter for every value
(`{{ resolved.variables | tojson }}`) rather than hand-writing JSON
punctuation - `tojson` handles escaping/comma-placement correctly regardless
of what's inside; the template author only writes the outer file shape by
hand.

**Considered and rejected: making the built-in default (D1) itself a
shipped Jinja2 template, unifying D1 and D3 into one render code path.**
Technically works (`tojson` makes it safe) and would mean `prepare()` always
renders *some* template, built-in or user-supplied, never a separate
`json.dumps()` path. Rejected for now: D1's projection is strata's own
maintained code, and as a `.j2` text file it loses mypy/IDE refactoring
support a Python dict-building function has - a typo in a built-in template
fails at render time, not at `mypy src` time. Kept split: Python/`json.dumps()`
for the maintained default (D1), Jinja2 only for the user-facing escape
hatch (D3).

**Same field, same mechanism, on both pipelines.** `output.template` has
nothing tool-specific in it: "render my own template against strata's
resolved values" is identical whether it sits on `ProvisionerModel`
(ADR-0022's infra path) or `ModuleModel` (ADR-0022's workload path, for
someone layering strata over an existing Helm chart's own `values.yaml` the
same way). Default (`output:` omitted) stays D1's built-in projection -
proven, backward-compatible with both real, working repos; the template is
opt-in for the layer-on/remove-cleanly case.

**D5: the D1-vs-D3 choice and D2's token step are one shared implementation
on `InfraIntegration` itself, not a per-tool `if` re-implemented in every
integration's own `prepare()`.** The Bicep finding above is the tell: if
each integration's `prepare()` independently checked
`provisioner.output.template` and independently wrote its backend config,
every new tool would have to remember to re-implement both checks
correctly, and "Bicep generates nothing by default" would have to be coded
as a special case someone has to think to add rather than something that
falls out for free. Instead, `InfraIntegration.prepare()` is implemented
once, concretely, on the base class:

```python
class InfraIntegration(Integration):
    def prepare(
        self, path: Path, *, resolved: ValueResolution, provisioner: ProvisionerModel,
        graph: ResolvedWorkspaceGraph, **kwargs: Any,
    ) -> Path:
        if provisioner.output and provisioner.output.template:
            rendered = render_output_template(provisioner.output.template, resolved=resolved, provisioner=provisioner)  # D3
            (path / strip_j2_suffix(provisioner.output.template)).write_text(rendered)
        else:
            for filename, content in self.default_output(resolved, provisioner, graph).items():  # virtual dispatch, not a branch
                (path / filename).write_text(content)

        if provisioner.backend:
            resolve_backend_tokens(provisioner.backend, resolved)  # D2, tool-agnostic

        return path

    def default_output(
        self, resolved: ValueResolution, provisioner: ProvisionerModel, graph: ResolvedWorkspaceGraph,
    ) -> dict[str, str]:
        """Filename -> content pairs to write when no output.template is set.
        Base default: nothing generated - Bicep's real behaviour (see below)."""
        return {}
```

`resolved: ValueResolution` (corrected - not `ResolvedValues`, which does not
exist in the codebase; see ADR-0022 D1a's safety note on why its one flat
`.values` dict needs care about which keys `build_run()` ever requests).
`ResolvedWorkspaceGraph` is defined in ADR-0022 (D1a) as a plain bundle of
already-resolved documents (`workspace`/`providers`/`topologies`/`resources`)
- not introduced here, just consumed here as the input D1's projection needs.

Each integration overrides only the one hook it needs a different answer
for - nothing else about `prepare()` is theirs to touch:

| Integration | overrides `default_output()`? |
| --- | --- |
| `TerraformIntegration` | yes - `planned_files(build_platform_projection(resolved, provisioner))` (D1) |
| `AnsibleIntegration` | yes - `{"extra-vars.json": json.dumps(...)}` |
| `BicepIntegration` | no - inherits the empty-dict base default, which *is* its correct "copy only" behaviour from v1's real `bicep_builder.py` |

This also means Bicep's "generate nothing" finding stops being a fact this
ADR has to separately remember to apply correctly per tool - it is simply
what happens when an integration writes zero lines of output-related code,
which is what v1's `BicepBuilder` already does.

**D4: ship at least one worked example, in the two tiers v1 already
established for every other authoring surface.** Confirmed against the real
package and a real workspace, not assumed:

- `strata`'s own package ships **built-in scaffolds**
  (`strata/templates/solution/` - the `strata init` dotfile/config scaffold
  - and `strata/templates/examples/{aks,compose}/` - full worked example
  solutions for the two major deployment shapes).
- A real workspace additionally carries **per-kind annotated examples**
  materialised into its own `.strata/templates/` (confirmed in haven's real
  checked-in `.strata/templates/module.yaml` etc.) - a copy-and-customise
  starting point living right next to the documents it's an example of,
  with a comment explaining how to use it (`"Copy this file and customize
  for your application module"`).

D3 should ship the same two tiers for `output.template`: at least one
example template under `strata/templates/examples/output/` in the package
itself (e.g. `variables.json.j2` - a small, single-purpose example showing
just variable substitution via `{{ resolved.values | tojson }}`, not the
full D1 projection), and the same file materialised into a real workspace's
`.strata/templates/` the way `module.yaml`/`workspace.yaml`/etc. already
are - so `output.template:` has a concrete, copyable starting point instead
of only a mechanism description in this ADR.

### Concrete walkthrough: Terraform

`TerraformIntegration` never implements `prepare()` itself - it only
provides the `default_output()` hook D5 already established; the shared
base `prepare()` handles the D3/D2 plumbing around it:

```python
class TerraformIntegration(InfraIntegration):
    def default_output(
        self, resolved: ValueResolution, provisioner: ProvisionerModel, graph: ResolvedWorkspaceGraph,
    ) -> dict[str, str]:
        payload = build_platform_projection(graph, provisioner)  # D1 - workspace/providers/.../tenant
        return {
            filename: json.dumps(data)
            for filename, data in planned_files(payload)  # skips empty categories
        }
```

`build_platform_projection()`/`planned_files()`/`resolve_backend_tokens()`
are real, sizeable pieces of design in their own right (v1's equivalents are
hundreds of lines across a dozen `_build_*_vars()` methods, plus ADR-0075's
own expression-resolution logic) - sketched here as named calls, not
expanded, since the shape (D1), what's real vs. speculative in shaping it
(D2), the escape hatch (D3), and the shared dispatch (D5) are the things
worth deciding now; the field-by-field mapping is implementation, not
architecture.

### The same contract for tools not built yet (description only - not built here)

With D5 in place, neither of these needs its own `prepare()` at all -
only a `default_output()` override (or none):

- **Ansible**: overrides `default_output()` -> writes
  `provisioner.properties.extra_vars` + `resolved.values` merged into an
  `extra-vars.json`. `plan()` would be a no-op or `--check` mode; `deploy()`
  runs `ansible-playbook`.
- **Bicep**: does **not** override `default_output()` at all - inherits the
  base class's empty-dict default. Confirmed against v1's real
  `bicep_builder.py` docstring: *"Unlike Terraform/Ansible, Bicep has no
  generated-vars-file concept (no `platform.json` projection) - ARM
  deployments consume `.bicep` files and an optional `parameters_file`
  directly from the copied source tree, and `BicepDeployer` reads its
  `configuration` block at deploy time."* v1's `BicepBuilder.build()` does
  exactly one thing: copy the provisioner source. Any parameters come from a
  static file already committed in the copied source tree, or a raw
  `configuration` dict read at *deploy* time (`deploy run`'s scope, not this
  ADR's). Real, standing evidence that "generate nothing" is a legitimate
  default, not a gap to fill - and with D5, it costs zero lines of code to
  express, rather than a branch someone has to remember to add.

Neither needs a single line changed in ADR-0022's orchestrator loop, nor in
the shared `InfraIntegration.prepare()` from D5 - only a new file
(`strata/integrations/ansible.py`/`bicep.py`) and one `_KNOWN` entry, the
exact pattern Phase 5/6 already proved out for Terraform/Compose/Helm.

### Value substitution differs in *kind*, not just timing, per output

Confirmed by reading the actual token shapes each builder emits, not
assumed:


| output | token shape | resolved when |
| --- | --- | --- |
| Terraform `.tfvars.json` | none - real values written directly | build time (non-secret only) |
| Compose `docker-compose.yml` | bare `${KEY}` | deploy time, via a `.env` file |
| Helm `values.yaml` | typed `${var:KEY}`/`${secret:KEY}`/`${feature:KEY}` (v1 ADR-0075) | deploy time - secrets via `--set-string` (never on disk), vars/features via a rewritten file |

None of this typed-expression resolution is designed here (see Remaining
Work) - the walkthrough above only covers writing `resolved.values`
directly (Terraform's case); Compose/Helm's own token
emission and deploy-time substitution still need their own design pass.

## Consequences

- Good: `OutputProfileModel`/`OutputFileModel` is **not** ported - checked
  against all three real workspaces available and found zero use of
  `format`/`emits`/`files`/`script` in any of them. An earlier draft of this
  section assumed the opposite ("no evidence it's over-built") without
  actually checking - corrected once real usage was checked.
- **Correction (found while grounding Phase 2, not at D2's original
  writing):** the "zero usage" check above was incomplete - it only
  covered `spoke`/`instance`, not `cfg-int-deployment`'s `control/workspace.yaml`,
  which has a real, load-bearing `output: {format: custom, emits: [features,
  variables, properties]}` block, complete with a real strata bug it hit and
  the fix that shipped for it (PR #309, "track written files by provisioner
  path"). D2's "don't port it" conclusion can no longer rest on "zero usage" -
  revisit this decision before Phase 2's namespaces/firewalls default
  emission ships unconditionally, in case a real `.tf` root needs the same
  emit-suppression `control/workspace.yaml` needed (an undeclared-variable
  warning on every `plan`/`apply`). Not redesigned here - flagged for its own
  pass, not folded silently into Phase 2.
- Good: the Jinja2 escape hatch (D3) reuses `strata/utils/templater.py`
  unchanged (its `_STRICT_ENV`) rather than introducing a second Jinja2
  setup - the codebase keeps exactly one hardened template engine, shared by
  scaffolding, builders, and now build-output templates.
- Bad: D2's "zero usage -> don't build it" conclusion and D3's "build the
  escape hatch anyway" conclusion look contradictory side by side - they are
  not, but the reasoning must stay attached to both or a future reader will
  reasonably ask why. D2 answers "do the two existing repos need
  customisation" (no). D3 answers a different question the same two repos
  cannot answer at all: "does someone layering strata over pre-existing,
  foreign infrastructure code need it" - unproven by usage, required by
  strata's own stated reversibility promise instead.
- Bad: D1's real tfvars shape (a full structural projection of the resolved
  platform graph) is a real, sizeable design surface this document only
  sketches (`build_platform_projection()` named, not expanded). An earlier
  draft assumed tfvars were just environment-declared variables - wrong,
  caught only by reading v1's real `TerraformBuilder` directly rather than
  trusting the simpler mental model.
- Neutral: this design is scoped to the infra/workload *output* problem
  specifically (a provisioner's/module's rendered config file). It is not a
  general-purpose templating layer for arbitrary build-time file generation
  - that would be `ModuleFileModel`'s existing STRATA_* substitution
  mechanism's problem to extend, a separate, already-real mechanism this ADR
  does not touch.
- Neutral: `ResolvedWorkspaceGraph` (ADR-0022 D1a) is very likely the same
  bundle a future deploy-manifest feature will need, not a separate
  `ResolvedManifestGraph` - it is just "the resolved workspace's documents",
  named for what it is rather than for Terraform's projection being its
  first consumer. That future feature can plausibly go further and reuse
  `build_platform_projection()`'s *output* too: v1's real deployment
  manifest embeds `artifacts.platform.content` - "the complete deployed
  configuration" - which is structurally the same shape D1 already
  produces. Combined with ADR-0022's own recorded resolution ("hash the
  rendered output directly, cheaper than reinventing a snapshot format"),
  this suggests the manifest feature may need **zero new graph-walking
  code** when it is eventually designed - only confirmed once that feature
  is actually scoped, not assumed here.

## Implementation Plan

Phased the same way ADR-0021 was - smallest proven-useful slice first,
category/mechanism boundaries chosen from real evidence already gathered in
this document, not evenly split for its own sake.

### Phase 1 - Core default projection: workspace, providers, topologies, resources

*Depends on: ADR-0021 (done - `TerraformIntegration`/`InfraIntegration` exist),
ADR-0022's `prepare()` signature (D1 there).*

The four categories with direct evidence in both real workspaces checked for
this ADR (haven, `cfg-deployment`) - every real `.tf` root actually
declares resources sitting on a topology, on a provider, in a workspace.
Grounded in v2's real current field names (checked directly, not assumed
from v1):

- `workspace` <- `WorkspaceMetaModel`/`WorkspaceSpecModel` (name, labels,
  tags, annotations - already ported field-for-field from v1's equivalent).
- `providers` <- `ProviderPropertiesModel` (`type`, `region`, `display_name`)
  per declared `spec.providers` entry.
- `topologies` <- `TopologySpecModel.components: list[TopologyComponentModel]`
  (`resource` reference, optional `modules`) and `.volumes:
  list[TopologyVolumeModel]`. Corrected after reading `topology_model.py`
  directly - an earlier draft placed `role`/`count` here, but those fields
  are on `WorkspaceResourceModel` instead (they describe the *resource
  instance*, not its topology placement) and are already covered by the
  `resources_by_category` bullet below.
- `resources_by_category` <- grouped by `ResourceSpecModel.category`, each
  entry carrying `provider_type`/`resource_type`/`subcategory`/`unit_cost`
  plus the workspace-level `WorkspaceResourceModel` override fields
  (`configuration`, `labels`, `tags`, `default_tags`/`custom_tags`,
  `firewalls`, `subnet`) merged on top - matches v1's real merge behaviour
  (workspace-level overrides win) rather than inventing a new precedence
  rule. **Deliberately excludes `custom`** - checked both real workspaces
  (haven, `cfg-int-deployment`) and found zero use of `custom` anywhere,
  same zero-usage pattern as `OutputProfileModel` (D2). `configuration` and
  `custom` are documented as separate channels for a reason (`configuration`:
  *"merged verbatim"* into the deployer's own structures; `custom`: *"for
  scripts or extensions"*, e.g. becomes env vars) - if both were merged into
  the projection there would be no way to keep a value out of `.tf`'s
  tfvars while still handing it to a script. This does mean a value needed
  by *both* Terraform and a script must be declared once, in
  `configuration` - not duplicated into `custom` too - since D3 template
  users can already reach `custom` directly from the full `resolved`
  context if they need it there instead. One rule, not a new mechanism:
  **if it needs to reach Terraform's tfvars, it goes in `configuration`,
  never in `custom`.**
- `required_variables`/`required_features`/`required_secrets` - **deferred
  out of Phase 1**, corrected after checking: no v2 model has a
  `references` field to walk for this. v1's structured `references:` block
  (real example: haven's `vaultwarden.yaml` `spec.references.secrets`) was
  authored against v1's schema; `module_model.py`'s own docstring confirms
  v2 deliberately did not port an equivalent ("checked against a real
  Environment in Phase 2, not an internal declared-keys list", ADR-0002).
  Building this manifest in v2 means regex-scanning resolved `configuration`/
  `backend`/`custom` values for `${var:}`/`${secret:}`/`${feature:}` tokens -
  a real, separate piece of design (shared with D2/Phase 3's token
  substitution, which needs the same token grammar), not a free side-effect
  of the four structural categories. Moved to Phase 2/3, whichever needs it
  first.

**One function per category, not a method on the model - this isn't a style
preference, the layering contract forces it.** ADR-0003's import-linter
contract has `strata.integrations` sitting *above* `strata.models`
(`commands > controllers > services > integrations > models > utils`). A
`WorkspaceModel.to_tfvars_payload()` method would mean the model layer
either has to know Terraform's specific output shape (an integration-layer
concern reaching down into models, backwards) or `strata.integrations`
reaching *up* into a method that exists only for its sake - neither
direction is allowed by the contract that already exists. The
`resources_by_category` merge proves it can't be one model's job even
without that rule: it needs a `ResourceModel`'s own fields *and* the
workspace's `WorkspaceResourceModel` override merged on top, "workspace
wins" - two documents at once, so there is no single model to hang the
method on regardless of layering. And this is the same lesson as
`BaseCommand` (`commands/run.py`'s own docstring: grew to 955 lines because
inheritance gives every subclass everything) and `BaseBuilder`
(`before_build`/`after_build` hooks most builders never use), aimed one
layer down: the moment a second consumer (Ansible's `extra-vars.json`, a
future diagram renderer) wants a different view of the same model, a method
glued onto the model becomes a dumping ground the same way a shared base
class does. Kept where it actually belongs - functions in the integration
that needs that specific shape, taking whatever models they need as plain
arguments (`_build_workspace_payload(workspace)`,
`_build_resources_payload(resources, workspace_overrides)`, ...) - the
same shape v1's own `_build_*_vars()` methods already had, just de-classed.

Built:

- `strata/integrations/terraform_projection.py` - `build_platform_projection()`
  (the four categories + manifest above) and `planned_files()` (skips any
  empty payload, matches Terraform's `*.auto.tfvars.json` naming
  convention). Deliberately a sibling module to `terraform.py`, not a method
  on `TerraformIntegration` itself - the projection needs the resolved
  document graph, which `TerraformIntegration` (an `Integration` subclass,
  no index access per ADR-0021 D2) is not allowed to touch directly;
  `default_output()` calls it with the `graph: ResolvedWorkspaceGraph`
  (ADR-0022 D1a) handed down from the orchestrator, the same way
  `resolved: ValueResolution` already is.
- `ResolvedWorkspaceGraph` (ADR-0022 D1a) - built and defined there, not
  here, since it changes `prepare()`'s own signature; this phase is simply
  its first real consumer.
- `InfraIntegration.prepare()` - the shared, base-implemented method (D5)
  that checks `provisioner.output.template` and otherwise calls
  `self.default_output(...)`; this phase implements the base method itself
  (previously undefined - ADR-0022 D1 only specified the ABC signature).
- `TerraformIntegration.default_output()` - the one hook Terraform overrides,
  wrapping `build_platform_projection()`/`planned_files()` into the
  `dict[str, str]` shape D5's base `prepare()` expects.
- Tests: argv/file-content assertions per category (stubbing a
  `ResolvedWorkspaceGraph` fixture shaped like haven's real workspace -
  workspace + providers + topology + resources, no
  namespaces/networks/firewalls/dns), empty-category skip behaviour, and
  workspace-level override-wins-over-resource-level merge for
  `resources_by_category`.

**Concrete function-by-function design** (field names checked directly
against the real v2 models, not v1's):

```python
# strata/integrations/terraform_projection.py

def _build_workspace_payload(workspace: WorkspaceModel) -> dict[str, Any]:
    """WorkspaceMetaModel/WorkspaceSpecModel -> name/labels/tags/annotations."""

def _build_providers_payload(providers: dict[str, ProviderModel]) -> dict[str, Any]:
    """name -> {type, region, display_name} per ProviderPropertiesModel."""

def _build_topologies_payload(
    topology_names: list[str] | None, topologies: dict[str, TopologyModel],
) -> dict[str, Any]:
    """name -> {type, components: [{resource, modules}], volumes: [...]}.
    workspace.spec.topology is optional (a workspace may have no grouping
    concept at all) - empty dict, not an error, when it is None."""

def _build_resources_payload(
    workspace_resources: list[WorkspaceResourceModel], resources: dict[str, ResourceModel],
) -> dict[str, Any]:
    """Grouped by ResourceSpecModel.properties.category. Each entry:
    ResourceSpecModel fields (provider_type/resource_type/subcategory/
    unit_cost) with the matching WorkspaceResourceModel's configuration/
    labels/tags/default_tags/custom_tags/firewalls/subnet merged on top -
    "workspace wins" (dict.update semantics, workspace's dict spread last).
    `custom` deliberately never read here (see the merge-fields bullet
    above) - only `configuration`."""

def build_platform_projection(
    graph: ResolvedWorkspaceGraph, provisioner: ProvisionerModel,
) -> dict[str, Any]:
    """The four-category dict (D1). `provisioner` is currently unused by
    Phase 1's four categories - accepted now so Phase 2/3's per-provisioner
    filtering (if any turns out to be needed) doesn't change this
    function's call sites again."""
    return {
        "workspace": _build_workspace_payload(graph.workspace),
        "providers": _build_providers_payload(graph.providers),
        "topologies": _build_topologies_payload(graph.workspace.spec.topology, graph.topologies),
        "resources_by_category": _build_resources_payload(graph.workspace.spec.resources or [], graph.resources),
    }

def planned_files(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """(filename, data) per non-empty top-level key - workspace.auto.tfvars.json,
    providers.auto.tfvars.json, topologies.auto.tfvars.json,
    resx_by_category.auto.tfvars.json. Naming matches Terraform's
    *.auto.tfvars.json auto-load convention (D1) - exact per-category
    filenames are implementation detail, not re-litigated here."""
```

`ResolvedWorkspaceGraph.providers`/`.topologies`/`.resources` are
`dict[str, Model]` (name -> model), not `list[Model]` - the orchestrator
(ADR-0022 `build_run()`) builds them by resolving each name in
`workspace.spec.providers`/`.topology`/`.resources[].resource` against
`index.get(...)`, so a dict keyed by that same name avoids every category
function re-doing its own name lookup/`next(r for r in resources if ...)`
search.

**Done when:** `build_platform_projection()`/`planned_files()` produce
correct output for a fixture shaped like haven's real `stack/workspace.yaml`
(the simplest real case: no modules/namespaces/networks feeding Terraform),
and `TerraformIntegration.prepare()` writes those files to `path`.

**Implemented.** One real gap found only during implementation, not
predicted at design time: `ValueResolution` (the real name for what this
document and ADR-0022 called `ResolvedValues` before that correction) is
defined in `strata.controllers.value_controller` — but `InfraIntegration.
prepare()`/`default_output()` (in `strata.integrations`) need to reference
it as a parameter type, and `strata.integrations` sits *below*
`strata.controllers` in the layering contract, so it cannot import from
there. Fixed the same way `ResolvedWorkspaceGraph` already was: moved
`ValueResolution`'s definition into `strata/integrations/resolved_context.py`
(alongside `ResolvedWorkspaceGraph`), and `value_controller.py` now imports
it from there instead of defining it — a controller importing a
lower-layer type is the direction the contract allows. Confirmed safe
before moving it: nothing else in the codebase imported `ValueResolution`
directly. `strata/integrations/resolved_context.py` docstring records this
reasoning so it isn't rediscovered from scratch next time a controller-layer
type needs to cross into `strata.integrations`.

Built exactly as designed above, plus this one addition:
`strata/integrations/resolved_context.py` (`ValueResolution`,
`ResolvedWorkspaceGraph`), `strata/integrations/terraform_projection.py`
(`build_platform_projection()`, `planned_files()`, and the four
`_build_*_payload()` helpers), `InfraIntegration.prepare()`/
`default_output()` (`strata/integrations/capabilities.py`),
`TerraformIntegration.default_output()` (`strata/integrations/terraform.py`).
Tests: `test_integrations_terraform_projection.py` (9 cases — the four
categories, empty-topology, workspace-override-wins merge, `enabled=False`
exclusion, `managed_by="provisioner"` exclusion, `planned_files()` naming/
skip behaviour), plus `prepare()`/`default_output()` coverage added to
`test_integrations_capabilities.py` and `test_integrations_terraform.py`.
Full check suite green: mypy clean, ruff clean, import-linter contract
kept, 866/866 tests passing (851 baseline + 15 new).

**Post-implementation correction, found while grounding Phase 2 in v1's
real `terraform_builder.py`:** `resources_by_category` was grouped by
`ResourceSpecModel.properties.category` and written as one combined file.
v1's real `_build_resources_by_category()` groups by `resource_type.lower()`
instead, wraps each group as `{"resources": {name: entry}}`, and
`_planned_files()` writes **one file per resource type**
(`resx_<type>.auto.tfvars.json`) — matching this ADR's own D1 text, which
the original implementation had not actually followed. Fixed: grouping key,
wrapper, and per-type file-splitting all now match v1's confirmed real
behaviour. 867/867 tests passing after the fix.

### Phase 2 - Remaining default categories: namespaces, firewalls, modules (dns/networks/tenant deferred further)

*Depends on: Phase 1 (same shape, more categories).*

Graded by real evidence, not built as one even batch of six — checked v1's
real `terraform_builder.py` directly (`_build_namespace_vars()`/
`_build_firewall_vars()`/`_build_module_vars()`/`_build_dns_vars()`/
`_build_network_vars()`/`_build_tenant_vars()`) for the true shape of each,
then graded each against real usage in haven/`cfg-int-deployment` before
scheduling it.

**2a - `namespaces`, `firewalls` - proven real usage, build these first.**
Both are used by haven in production: 6 real namespaces
(`hearth`/`system`/`immich`/`media`/`documents`/`finance`), 2 real firewalls
(`haven_fw_hetzner_hearth`/`haven_fw_hetzner_forge`), each referenced from a
`WorkspaceResourceModel.firewalls` list. v1's real shapes:

- `namespaces` <- `{name: {description, labels, tags, modules: [module
  names]}}`. v2 equivalent: `NamespaceModel.meta` (`annotations`/`labels`/
  `tags`) + `NamespaceModel.spec.modules: list[ModuleReferenceModel]`
  (already confirmed field on the real model) mapped to `[m.module for m in
  spec.modules]` (field name to confirm against v2's real
  `ModuleReferenceModel` before implementing - not assumed identical to
  v1's `m.module`).
- `firewalls` <- `{name: {description, labels, tags, rules: {reset,
  defaults, deny, allow}}}`. v2 equivalent: `FirewallModel.meta` +
  `FirewallModel.spec.{reset, defaults, deny, allow}` (already confirmed
  fields on the real model from Phase 1's own reading of
  `platform_artifact_model.py`'s `PlatformFirewallModel` mirror).

Both need a `graph.namespaces: dict[str, NamespaceModel]`/
`graph.firewalls: dict[str, FirewallModel]` addition to
`ResolvedWorkspaceGraph` (ADR-0022 D1a) - resolved from
`workspace.spec.namespaces`/`.firewalls` the same way `providers`/
`topologies`/`resources` already are, plus the orchestrator resolving
every `WorkspaceResourceModel.firewalls` reference (a resource points at
firewalls by name; the graph must hold the referenced `FirewallModel`
documents, not just the names, the same "already-resolved" contract the
other three categories already follow).

**Done when:** both have the same fixture-based test coverage as Phase 1's
four, built and merged in one PR/commit each (not together) - and a fixture
shaped like haven's real `stack/workspace.yaml` (now including its real
namespaces/firewalls) produces the correct two additional files.

**Implemented.** `ResolvedWorkspaceGraph` (ADR-0022 D1a) gained
`namespaces: dict[str, NamespaceModel]`/`firewalls: dict[str, FirewallModel]`;
`terraform_projection.py` gained `_build_namespaces_payload()`/
`_build_firewalls_payload()`, wired into `build_platform_projection()`.
`FirewallRuleModel`'s `from_`/`from` alias needed `by_alias=True` on the
`allow`/`deny` rule dumps (confirmed against v1's own
`model_dump(..., by_alias=True)`) - without it the field would have
serialised as `from_`, which no real `.tf` module declares. 5 new tests
added (14 total in `test_integrations_terraform_projection.py`). Full
check suite green: 871/871 tests passing.

**2b - `modules` - checked, zero evidence, skipped.** haven attaches 5+
real modules (`vaultwarden`, `caddy`, `authentik`, `portainer`, `wud`, plus
Forge's Helm-deployed set), but exclusively via `Namespace.spec.modules` -
never `TopologyComponentModel.modules`. Checked all six real workspaces
available for this ADR (haven, and `cfg-int-deployment`'s `control`/
`customer`/`ring`/`spoke`/`instance`): every single real topology component
is bare `{resource: <name>}` - zero use of `TopologyComponentModel.modules`
anywhere. Since ADR-0022 D5-D7 already established Compose/Helm modules
never reach `TerraformIntegration.prepare()` at all (they go through the
entirely separate `prepare_namespace()` pipeline, keyed off
`Namespace.spec.modules`), a `modules` category in Terraform's default
projection would have **zero real consumers** in any workspace checked -
same "don't build it until real evidence shows up" discipline as
`OutputProfileModel` (D2). **Skipped, not built** - v1's real shape
(`{name: {repository, source_path, target_path, description, labels, tags,
properties}}`) is recorded above only as a reference for if/when a real
`TopologyComponentModel.modules` attachment shows up.

**Done when:** N/A - explicitly marked "no evidence, skipped" per this
sub-phase's own stated exit condition. Revisit only if a real workspace is
found attaching a module directly to a `TopologyComponentModel`.

**2c - `dns`, `networks` - built despite no proven Terraform-consuming
usage yet, on explicit request; `tenant` still deferred (no fixture data
to ground it against at all).** No `dns_zones`/`tenant` in any workspace
checked (haven or any of `cfg-int-deployment`'s five stacks).
`cfg-int-deployment`'s own `spoke/network.yaml` header is explicit that
networks validate CIDR syntax/uniqueness but do **not** currently feed the
Terraform root (`spoke_resx` stays one opaque `managed_by: provisioner`
resource, no per-subnet `subnet:` reference attached to anything) - built
anyway since a real `NetworkModel`/`DnsModel` schema exists to ground the
shape against, even without a real consumer yet. Adapted from v1's real
shapes, not copied verbatim - v2's schema differs in one load-bearing way:
`DnsRecordModel.value` is a single Value-binding string (may itself
contain `${var:}`/`${secret:}`/`${feature:}` tokens, ADR-0002), not v1's
separate `value`/`var`/`secret`/`output_key` fields - v2 never ported
`output_key` at all (ADR-0006, no shared runtime Context store yet), so
there is no `dns_secret_records`/`dns_output_records` bucketing to
reproduce; `record.value` (and `SubnetModel.cidr`/
`NetworkDefinitionModel.address_space`) are written as-is, tokens
unresolved - a known, tracked gap (see Remaining Work), not silently
dropped. `tenant` remains unbuilt: zero real workspaces reference a
`TenantModel` at all, so there is no fixture to ground even its v1-derived
shape (`{strata_tenant: {code, name, zones, onboarded?, configuration?}}`)
against - revisit once one does.

**Implemented (dns/networks).** `ResolvedWorkspaceGraph` gained `dns: dict[str,
DnsModel]`/`networks: dict[str, NetworkModel]`; `terraform_projection.py`
gained `_build_dns_payload()`/`_build_networks_payload()`, wired into
`build_platform_projection()`. 4 new tests (18 total in
`test_integrations_terraform_projection.py`). Full check suite green:
875/875 tests passing.

**Done when:** dns/networks - done, see above. `tenant` - N/A until a real
workspace links a `TenantModel`; intentionally has no target date.

### Phase 3 - `${var:}`/`${secret:}`/`${feature:}` substitution in provisioner config (D2)

*Depends on: Phase 1 (touches the same `prepare()` call site).*

- `resolve_expr_tokens()` - walks `provisioner.backend.configuration`/
  `.configuration`/`.properties` (whichever are set) and resolves
  `${var:KEY}`/`${secret:KEY}`/`${feature:KEY}` tokens against `resolved:
  ValueResolution`, leaving any non-token string untouched. v1 ADR-0075 is
  the starting reference for the token grammar - not assumed identical
  without checking its exact regex/parsing rules first.
- Wired into `TerraformIntegration.prepare()`'s backend-writing branch
  (already sketched in this ADR's Terraform walkthrough).

**Done when:** `cfg-deployment`'s real `backend.configuration` block
(the concrete example already quoted in this ADR) round-trips correctly
against a fixture `ValueResolution`, including the secret-shaped
`tf_state_storage_account`-style keys.

### Phase 4 - The Jinja2 template escape hatch (D3)

*Depends on: Phase 1 (the default this overrides), Phase 3 (shares the same
"resolve tokens against `resolved`" reasoning, applied via Jinja2 context
instead of string-token substitution).*

- `OutputModel` (new, small) - `template: str | None`, added to both
  `ProvisionerModel.output` and `ModuleModel.spec.output`. Path resolution
  reuses `SourceModel`/`ModuleFileModel.source`'s existing `@repo/`
  handling - not a new resolution mechanism.
- `InfraIntegration.prepare()`'s `if provisioner.output and
  provisioner.output.template:` branch (D5, already implemented in Phase 1
  as the base method's skeleton) - this phase fills in the real
  `render_output_template()` body, rendering via `strata.utils.templater`'s
  `_STRICT_ENV` - reused, not reimplemented; confirm its exact public
  surface (module-level constants today, may need a small function wrapper
  to call from outside `templater.py` without reaching into "private"
  `_STRICT_ENV`) before assuming direct import is the final shape. No
  per-integration change needed - every `InfraIntegration` subclass gets the
  escape hatch for free via the shared base `prepare()`.

**Done when:** a hand-written `strata.tfvars.json.j2` fixture (using
`{{ resolved.values | tojson }}`) renders correctly and a missing
variable raises instead of silently rendering `{{ var }}` into the output
file.

### Phase 5 - Shipped example templates (D4)

*Depends on: Phase 4 (needs `output.template` to exist and work first).*

- `strata/templates/examples/output/variables.json.j2` - package-shipped,
  alongside the existing `strata/templates/examples/{aks,compose}/`.
- The same file materialised into a real workspace's `.strata/templates/`
  (wherever that materialisation already happens for `module.yaml`/
  `workspace.yaml`/etc. - reuse that mechanism, do not invent a second one).

**Done when:** a fresh `strata init`-created workspace's `.strata/templates/`
contains `variables.json.j2` alongside the existing per-kind examples, with
the same "copy this and customize" comment convention.

## Remaining Work (cross-phase, not yet scheduled)

- Compose/Helm's own deploy-time value substitution (`${KEY}`/`${var:}`/
  `${secret:}`/`${feature:}`) - documented as existing and differing in this
  ADR's own table, but not designed - needs its own phase once
  `prepare_namespace()` (ADR-0022 D7) is being implemented, not before.
- Ansible/Bicep's own `prepare()` bodies - sketched only (see "the same
  contract for tools not built yet" above); real implementation waits for
  those `Integration` classes to exist at all (no evidence of demand yet -
  Ansible is Tier 2 per `/memories/repo/v1-consumer-usage.md`).
- **D2 (`OutputProfileModel`) needs a real revisit** - real, load-bearing
  usage found in `cfg-int-deployment`'s `control/workspace.yaml`
  (`output: {format: custom, emits: [...]}`), contradicting the "zero
  usage" evidence D2 was decided on. Not redesigned yet - flagged here so
  it isn't silently reintroduced piecemeal while building Phase 2's
  additional default categories.
- **Build-time `features`/`variables` resolved-value emission** (v1's
  `flags.auto.tfvars.json`/`variables.auto.tfvars.json`, confirmed real via
  `terraform_builder.py`'s `_build_feature_flags_vars()`/
  `_build_flat_variables()`) - a genuinely separate mechanism from D1's
  four structural categories, D2's backend-token substitution, and D3's
  Jinja2 escape hatch: resolves *constant/environment*-store
  variables/features directly into flat `{key: value}` build-time tfvars,
  while integration-backed stores (Infisical/Vault/...) are deliberately
  skipped and deferred to deploy-time via `ValueResolution` instead. Not
  designed here - found while investigating D2's real usage, not yet
  scoped into a phase.
- **`dns`/`networks`'s `${var:}`/`${secret:}`/`${feature:}` tokens are
  written unresolved** - `DnsRecordModel.value`/`SubnetModel.cidr`/
  `NetworkDefinitionModel.address_space` may contain these tokens
  (ADR-0002), but Phase 2c's `_build_dns_payload()`/
  `_build_networks_payload()` write them as-is. Phase 3's
  `resolve_expr_tokens()` only wires into `provisioner.backend`/
  `.configuration`/`.properties` today - extending it to these two
  categories' token-bearing fields is real, tracked work, not done yet.

