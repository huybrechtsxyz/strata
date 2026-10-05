# Build Output Rendering — Default Projection and a Jinja2 Escape Hatch

- Status: accepted
- Date: 2026-09-24
- Related: [ADR-0022](0022-strata-build-run.md) (`strata build run` - the
  consumer: `InfraIntegration.prepare()`/`prepare_namespace()` call into this
  design to produce whatever they write to disk), [ADR-0021](0021-integration-layer.md)
  (the `InfraIntegration` ABC this document's rendering logic sits behind)
- See also: [docs/work/build-pipeline-status.md](../work/build-pipeline-status.md)
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

| Integration            | overrides `default_output()`?                                                                                             |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `TerraformIntegration` | yes - `planned_files(build_platform_projection(resolved, provisioner))` (D1)                                              |
| `AnsibleIntegration`   | yes - `{"extra-vars.json": json.dumps(...)}`                                                                              |
| `BicepIntegration`     | no - inherits the empty-dict base default, which *is* its correct "copy only" behaviour from v1's real `bicep_builder.py` |

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


| output                       | token shape                                                       | resolved when                                                                                |
| ---------------------------- | ----------------------------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Terraform `.tfvars.json`     | none - real values written directly                               | build time (non-secret only)                                                                 |
| Compose `docker-compose.yml` | bare `${KEY}`                                                     | deploy time, via a `.env` file                                                               |
| Helm `values.yaml`           | typed `${var:KEY}`/`${secret:KEY}`/`${feature:KEY}` (v1 ADR-0075) | deploy time - secrets via `--set-string` (never on disk), vars/features via a rewritten file |

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
  covered `spoke`/`instance`, not `config-deploy`'s `control/workspace.yaml`,
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

