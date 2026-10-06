# Provisioner Source Dependencies (materialise a source with no execution step) — Work

- Status: in-progress — decision made ([ADR-0029](../decisions/0029-provisioner-source-dependencies.md),
  `ProvisionerModel.depends_on`), not yet built. This doc now tracks only
  the phased implementation; the decision itself (why `depends_on` over
  the 3 alternatives) is ADR-0029's, not repeated here.
- Last updated: 2026-10-06

## Overview

A real IaC team hit this debugging a genuine Terraform module-resolution
failure (`Unable to evaluate directory symlink: lstat ../../components: no
such file or directory`) the first time a real `terraform init` ever ran
against `build run` output in their pipeline — previously masked, because
`deploy run --dry-run` never touches Terraform at all.

**Root cause, confirmed directly against `strata/controllers/build_controller.py`.**
`build_run()`'s only materialisation loop is:

```python
for step in ordered_by_depends_on(workspace.spec.execution or []):
    provisioner = find_provisioner(workspace, step.provisioner)
    ...
    source_path = sync_source(context.root, build_path, provisioner.source, remotes)
```

(see [build_controller.py](../../src/strata/controllers/build_controller.py),
`build_run()`'s step loop). It iterates `workspace.spec.execution`, never
`workspace.spec.provisioners` directly — a `ProvisionerModel` declared with
no `ProvisioningStepModel` referencing it (`step.provisioner == name`) is
never passed to `sync_source()` at all, confirmed empirically too: a
`source_path`-only provisioner (no `backend`, no execution step, meant
purely to stage a shared Terraform module library so a sibling
provisioner's own `.tf` files can resolve `source = "../../components/x"`
relative paths) produced zero output directory after `build run`.

`WorkspaceSpecModel` has no separate staging/sync concept either — only
`providers`/`provisioners`/`execution`/`topology`/`resources`
([workspace_model.py](../../src/strata/models/workspace_model.py)) — so
there is currently no way to express "materialise this source, but it's
not a deployable step" at all.

**This is the other half of a gap ADR-0022 D3 already found but did not
fully close.** D3 (see
[0022-strata-build-run.md](../decisions/0022-strata-build-run.md)) found
the exact same real workspace shape (`config-deploy`'s
`stacks/spoke/workspace.yaml`, a `core_modules` copy-only provisioner) and
fixed *where* a materialised source lands —
[source_sync.py](../../src/strata/controllers/source_sync.py)'s destination
mirrors each source's own `source_path` rather than the step/provisioner
name, specifically so sibling relative-path composition
(`source = "../../core/terraform/components/aks"`) resolves correctly.
That half is built (confirmed in
[build-command.md](build-command.md)'s status table: "mirrors each
source's own `source_path` as its build-directory destination ... makes
sibling relative composition resolve correctly with no cross-provisioner
awareness needed"). What D3 did **not** fix is *whether a provisioner's
source is materialised at all* when nothing in `execution` ever names
it — that gap is this document's scope.

### Workaround shipped (fragile, in production today)

The reporting team gave each staging provisioner a real execution step —
with a `scope: stage` label
(`ProvisioningStepModel.scope`, a free-form filter field, not a validated
enum — see
[provisioning_model.py](../../src/strata/models/provisioning_model.py)) —
and a borrowed `targets:` entry, since
`ProvisioningStepModel.targets` is `min_length=1` and cannot be empty. Their
real apply invocation always passes `--scope infra` so these steps are
never planned/applied for real.

It works, but:

- These aren't deployable steps — they're materialisation dependencies, and
  the schema has no way to say that directly.
- Nothing stops a future `deploy run` invocation from forgetting
  `--scope infra` and attempting to plan/apply a provisioner that was only
  ever meant to stage shared Terraform modules.
- The borrowed `targets:` entry is a lie forced by a `min_length=1`
  constraint that exists for a different reason (a real deployable step
  must act on *something*).

## Design (decided — [ADR-0029](../decisions/0029-provisioner-source-dependencies.md))

### D1: `ProvisionerModel` gains `depends_on: list[PlatformName] | None`

```python
depends_on: list[PlatformName] | None = Field(
    None,
    description="Names of other Provisioners in this same workspace whose 'source' must be "
    "materialised before this one's, independent of 'execution'/'targets'. A dependency "
    "provisioner named here does not need its own execution step — it is synced purely to "
    "make its files available on disk for this provisioner's own source code to compose "
    "(e.g. a relative Terraform module path) at build time. Never implies an execution order "
    "or deploy-time relationship by itself — see ProvisioningStepModel.depends_on for that.",
)

@model_validator(mode="after")
def validate_no_self_dependency(self) -> "ProvisionerModel":
    if self.depends_on and self.name in self.depends_on:
        raise ValueError(f"Provisioner '{self.name}' cannot depend on itself.")
    return self
```

Existence and cycle checking need sibling awareness (all of
`workspace.spec.provisioners` at once), so — same pattern
`WorkspaceSpecModel.validate_execution()` already uses for
`ProvisioningStepModel.depends_on` — a new cross-field validator on
`WorkspaceSpecModel` does that part:

```python
@model_validator(mode="after")
def validate_provisioner_depends_on(self) -> "WorkspaceSpecModel":
    names = {p.name for p in self.provisioners}
    errors = []
    for p in self.provisioners:
        for dep in p.depends_on or []:
            if dep not in names:
                errors.append(f"Provisioner '{p.name}': depends_on '{dep}' is not a declared provisioner.")
    if errors:
        raise ValueError("; ".join(errors))
    topological_order(names, {p.name: p.depends_on or [] for p in self.provisioners})  # D2 — raises on a cycle
    return self
```

**Naming decision: reuse `depends_on`, not `requires`/`stage_with`.** The
issue's own text offers all three to "avoid confusion with
`ProvisioningStepModel.depends_on`." Proposed resolution: keep
`depends_on` on both models. The *meaning* is identical ("these other
named things must be processed first") — only the namespace differs
(`provisioners[].depends_on` names other provisioners;
`execution[].depends_on` names other steps), and YAML's own nesting
already disambiguates which graph a given `depends_on` walks. Introducing
a second word for the same concept would cost more reader clarity than it
buys. `requires` was considered and rejected specifically: ADR-0002
(["Requirement" rejected as a schema concept](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md))
already relegated a similarly-named idea (a `spec.references`-style
declared-requirement field) to "rejected" — reusing that word for an
unrelated mechanism risks a reader conflating the two.

### D2: Extract one shared topological-order/cycle-check utility

Today the Kahn's-algorithm cycle check is duplicated in two places that
both deliberately avoid re-validating the other's work:

1. `provisioning_model.validate_provisioning_steps()` — Phase 1, detects a
   cycle in `ProvisioningStepModel.depends_on`, discards the order.
2. `build_controller.ordered_by_depends_on()` — build-time, assumes
   already-acyclic input (per its own docstring), returns the order.

This proposal adds two more call sites with the identical shape (provisioner
names instead of step names): D1's `validate_provisioner_depends_on()`
above, and D3's materialisation walk below. Rather than a third/fourth
copy of the same ~15-line Kahn's-algorithm block, extract one generic
helper — e.g. `strata/utils/dependency_order.py`:

```python
def topological_order(names: Iterable[str], depends_on: Mapping[str, Sequence[str]]) -> list[str]:
    """Kahn's algorithm. Returns `names` ordered so every dependency in
    `depends_on[name]` precedes `name`. Raises ValueError naming the cycle
    if one exists."""
```

`validate_provisioning_steps()`'s existing cycle check and
`ordered_by_depends_on()` both become thin callers of this (behaviour-
preserving refactor — see Implementation Plan step 1); the new
provisioner-level validator and materialiser (D3) call the same function,
so a cycle in either graph is reported identically.

### D3: `build_run()` materialises a provisioner's dependency chain before its own

```python
def materialise_provisioner_sources(
    context: SolutionContext,
    build_path: Path,
    remotes: dict[str, SolutionRemoteModel],
    workspace: WorkspaceModel,
    provisioner: ProvisionerModel,
    materialised: dict[str, Path],
    *,
    dry_run: bool = False,
    on_step: Callable[[str], None] | None = None,
) -> Path | None:
    """Sync `provisioner.depends_on`'s sources first (plain DFS with a
    memoized `materialised` set — deliberately **not** a full
    `topological_order()` pass here: sibling sources are independent file
    copies into separate directories with no cross-reads at sync time, so
    the *order* two unrelated dependencies are copied in never matters —
    only *reachability* (does this source get synced at all) does. D1's
    Phase-1 validator still runs the full cycle check up front, so this
    recursion never has to detect one itself), then `provisioner`'s own,
    exactly once each for the whole `build_run()` invocation —
    `materialised` is shared across every step so two steps whose
    provisioners both depend on the same staging provisioner (the issue's
    own `iac_components`/`iac_primitives` example) sync it only once.
    """
    if provisioner.name in materialised:
        return materialised[provisioner.name]
    for dep_name in provisioner.depends_on or []:
        materialise_provisioner_sources(
            context, build_path, remotes, workspace, find_provisioner(workspace, dep_name),
            materialised, dry_run=dry_run, on_step=on_step,
        )
    source_path: Path | None = None
    if provisioner.source is not None:
        if dry_run:
            if on_step:
                on_step(f"would materialise provisioner '{provisioner.name}' source (dependency)")
        else:
            source_path = sync_source(context.root, build_path, provisioner.source, remotes)
            if on_step:
                on_step(f"materialised provisioner '{provisioner.name}' source at {source_path}")
    materialised[provisioner.name] = source_path if source_path is not None else build_path / provisioner.name
    return materialised[provisioner.name]
```

The step loop seeds one `materialised: dict[str, Path] = {}` before
iterating `ordered_by_depends_on(workspace.spec.execution or [])`, and
calls `materialise_provisioner_sources()` for each step's own provisioner
(which recurses into its `depends_on` first) instead of calling
`sync_source()` directly — the rest of the loop (`resolve_integration()`,
`integration.prepare()`, `output.template` handling) is unchanged.

**A dependency-only provisioner is never passed to `resolve_integration()`/
`integration.prepare()`.** It is sync-only by construction — nothing
renders a `.tfvars.json`/`values.yaml` for it, and it never needs an
`Integration` binding at all, matching the issue's own framing ("these
aren't deployable steps, they're materialisation dependencies").

### D4: Migration — the fake execution step is no longer needed

Before (today's shipped workaround):

```yaml
spec:
  provisioners:
    - name: spoke_infra
      tool: terraform
      source: {...}
    - name: iac_components
      tool: terraform
      source: {...}
    - name: iac_primitives
      tool: terraform
      source: {...}
  execution:
    - name: spoke_infra_step
      provisioner: spoke_infra
      targets: [spoke_network]
    - name: iac_components_step        # fake — never deployable
      provisioner: iac_components
      targets: [spoke_network]         # borrowed, meaningless
      scope: stage                     # relies on --scope infra never slipping
    - name: iac_primitives_step
      provisioner: iac_primitives
      targets: [spoke_network]
      scope: stage
```

After (this proposal):

```yaml
spec:
  provisioners:
    - name: spoke_infra
      tool: terraform
      source: {...}
      depends_on: [iac_components, iac_primitives]
    - name: iac_components             # no execution step at all
      tool: terraform
      source: {...}
    - name: iac_primitives              # no execution step at all
      tool: terraform
      source: {...}
  execution:
    - name: spoke_infra_step
      provisioner: spoke_infra
      targets: [spoke_network]
```

`iac_components`/`iac_primitives` are no longer reachable as steps at all —
`--scope infra`/`--scope stage` filtering has nothing to accidentally
include or exclude them from, which removes the "forgot `--scope infra`"
footgun entirely, as the issue itself suggests.

## Alternatives Considered

- **`ProvisioningStepModel.provisioner: PlatformName` → `provisioners:
  list[PlatformName]`** (let one execution step bind several
  provisioners). Rejected. This only works once something decides *which*
  entry in the list is the one actually planned/applied — "first entry
  wins" is implicit and silently changes behaviour if the YAML list is
  ever reordered, arguably a worse footgun than the one being fixed here;
  spelling the role out explicitly per entry just reinvents `depends_on`
  one level deeper, nested inside a model whose whole job (ADR-0011) is
  "what runs, in what order," not "what tool composition does this
  provisioner's own source code need." It also doesn't remove the
  duplication problem: two different deployable provisioners that both
  need the same staging provisioner would each have to list it in their
  own step's `provisioners:`, same as the next alternative.
- **Keep `provisioner:` singular, add an additive
  `ProvisioningStepModel.materialise: list[PlatformName]`** (a refined,
  unambiguous version of the above — no first-entry magic, explicit
  sync-only list alongside the real `provisioner:`). Rejected, but closer:
  fixes the ordering-ambiguity problem above, but still duplicates the
  declaration on every consuming step (no natural dedupe point), and
  cannot express a **transitive** staging chain at all — if `iac_components`
  itself needed another provisioner materialised first, there is no step
  attached to `iac_components` to hang a second `materialise:` list off of;
  every consumer's step would have to hand-flatten the whole transitive
  set itself. `depends_on` on `ProvisionerModel` gets transitive
  composition for free, via the same recursion D3 already needs.
- **A `ProvisionerModel.deployable: bool = True` (or `role: provision |
  stage`) classification flag, with `build_run()` unconditionally syncing
  every non-deployable provisioner once, up front — no dependency graph at
  all.** The strongest real alternative found. No cycle detection, no
  recursion, handles a transitive staging chain for free (every staging
  provisioner in the chain just gets the same flag, order never matters
  since reachability is unconditional rather than declared). Real
  trade-off against the chosen design: it drops the explicit "`spoke_infra`
  needs exactly these two" documentation link a reader gets from
  `depends_on` — a staging provisioner's *reason to exist* becomes
  implicit/inferred (a `description:` field, or tribal knowledge) rather
  than traceable from the consuming provisioner's own declaration. It also
  syncs a declared-but-currently-unused staging provisioner unconditionally
  every build (harmless, just not lazy). Not chosen — given how much this
  repo's real consumers rely on workspace YAML being self-documenting
  (`/memories/repo/v1-consumer-usage.md`), the explicit relationship is
  judged worth the small added implementation cost (one field, one
  validator, one recursive helper — not a large delta over this
  alternative's flat loop). Worth reconsidering as a simplification if
  real usage shows `depends_on` chains never go more than one level deep
  and the "why" is always obvious from naming alone.
- **A separate `staging:`/`sync:` top-level list, distinct from
  `provisioners:`.** Rejected — would duplicate `ProvisionerModel`'s whole
  `source`/`tool`/`backend`/`integration` shape for an entry that is
  structurally the same kind of thing, just never bound to an execution
  step. One list, one model, with an optional `depends_on`, is the smaller
  schema surface.
- **Doing the dependency walk inside `sync_source()` itself.** Rejected —
  `sync_source()` operates on one `SourceModel` at a time with no
  workspace/provisioner-graph awareness, matching the same "integrations
  don't touch `DocumentIndex`" discipline ADR-0021 D2 already established
  one layer up. The walk belongs in the orchestrator
  (`build_controller.py`), which already holds the whole
  `workspace.spec.provisioners` list and the `find_provisioner()`/
  `ordered_by_depends_on()` helpers this reuses.
- **Field names `requires`/`stage_with` instead of `depends_on`.**
  Considered per the issue's own suggestion; rejected — see D1's naming
  decision above.

## Open Questions

- Should a dependency provisioner that resolves to a sync/GitOps tool
  (`source is None`) be rejected in `depends_on` (nothing to materialise)
  or silently permitted as a no-op? Leaning toward "permitted" — mirrors
  `ProvisionerModel.validate_source_required_unless_sync()`'s existing
  leniency elsewhere on this same model — but not resolved here.
- Should `depends_on` be restricted to provisioners with **no** execution
  step (purely staging), or freely allowed on any provisioner, including
  one that already has its own step? Current proposal: unrestricted — a
  provisioner's execution-step membership and its `depends_on`
  materialisation needs are orthogonal facts, matching ADR-0011's
  "topology and provisioning are decoupled" precedent elsewhere in this
  model family. Not resolved here; no evidence yet of a real case needing
  the restriction.
- Does `strata graph` need a new edge type for this? Not addressed here —
  `strata graph` (see
  [v1-consumer-usage.md](../../docs/work/build-pipeline-status.md) via
  memory, or `graph_controller.py` directly) only walks `References()`-
  annotated cross-document fields today; `depends_on` here is a
  same-document, same-list name reference, the same category
  `ProvisioningStepModel.provisioner`/`.depends_on` already are, neither of
  which appears in `strata graph` output today either.

## Implementation Plan

Five phases, in dependency order (each phase ends with the repo's full
check suite — `mypy src/strata`, `ruff check src/ tests/`, `pytest -q`,
`lint-imports` — green before starting the next, per
`/memories/repo/conventions.md`'s "Checks (run after any model/service
change)" rule).

### Phase 1 — extract the shared topological-order/cycle-check utility (pure refactor, no schema change)

- New file `src/strata/utils/dependency_order.py`:
  ```python
  def topological_order(names: Iterable[str], depends_on: Mapping[str, Sequence[str]]) -> list[str]:
      """Kahn's algorithm. Returns `names` ordered so every dependency in
      `depends_on[name]` precedes `name`. Raises ValueError naming the
      unresolved/cyclic names if a cycle exists."""
  ```
- Rewrite `provisioning_model.validate_provisioning_steps()`'s inline Kahn's-
  algorithm block (cycle check only, order discarded) to call this —
  behaviour must be identical, same `ValueError` message shape the existing
  tests already assert on.
- Rewrite `build_controller.ordered_by_depends_on()` to call this too (now
  genuinely a one-line wrapper — it already documented itself as "the same
  shape `validate_provisioning_steps()` uses").
- New file `tests/strata/utils/test_utils_dependency_order.py` — acyclic
  order, diamond shape, a real cycle (message contains every node in the
  cycle), an unknown name in `depends_on` (should not be this function's
  job to catch — confirm it only orders/cycle-checks names it's given,
  existence-checking stays the caller's job, matching `validate_provisioning_steps()`'s
  own existing two-pass structure: unknown-name errors raised before the
  cycle check ever runs).
- No behaviour change expected anywhere else — rerun
  `tests/strata/models/test_models_provisioning.py::test_validate_provisioning_steps_rejects_cycle`
  and `tests/strata/controllers/test_build_controller.py::test_ordered_by_depends_on_handles_a_diamond`
  (plus siblings) unchanged to confirm.

### Phase 2 — `ProvisionerModel.depends_on` + self-dependency validation

- `src/strata/models/provisioning_model.py`: add the field (D1's schema
  above) directly below `ProvisionerModel.output`, plus a
  `validate_no_self_dependency` `model_validator(mode="after")` mirroring
  `ProvisioningStepModel`'s own method of the same name immediately below
  it in the same file.
- New tests in `tests/strata/models/test_models_provisioning.py` (next to
  the existing `test_provisioning_step_accepts_depends_on()`/
  `test_validate_provisioning_steps_rejects_cycle()`):
  `test_provisioner_accepts_depends_on()`,
  `test_provisioner_rejects_self_dependency()`.
- No cross-field validation yet (Phase 3) — a `ProvisionerModel` built in
  isolation (as these model-level tests do) cannot check that a
  `depends_on` name actually resolves to a sibling; that needs
  `WorkspaceSpecModel`'s sibling awareness.

### Phase 3 — `WorkspaceSpecModel.validate_provisioner_depends_on()` (unknown-name + cycle, Phase 1 cross-document validation)

- `src/strata/models/workspace_model.py`: new `model_validator(mode="after")`
  next to the existing `validate_execution()`, same shape:
  ```python
  @model_validator(mode="after")
  def validate_provisioner_depends_on(self) -> "WorkspaceSpecModel":
      names = {p.name for p in self.provisioners}
      errors = []
      for p in self.provisioners:
          for dep in p.depends_on or []:
              if dep not in names:
                  errors.append(f"Provisioner '{p.name}': depends_on '{dep}' is not a declared provisioner.")
      if errors:
          raise ValueError("; ".join(errors))
      topological_order(names, {p.name: p.depends_on or [] for p in self.provisioners})  # raises on a cycle
      return self
  ```
- Import `topological_order` from Phase 1's new `strata.utils.dependency_order`.
- New tests in `tests/strata/models/test_models_workspace.py` (next to
  whatever covers `validate_execution()` today):
  `test_workspace_rejects_unknown_provisioner_depends_on()`,
  `test_workspace_rejects_provisioner_depends_on_cycle()`,
  `test_workspace_accepts_provisioner_with_no_execution_step_but_a_depends_on_consumer()`
  (the actual shape from the issue — a provisioner with `depends_on` set but
  no step references the *dependency* provisioner itself; confirms the
  schema no longer forces a fake step for it).

### Phase 4 — `build_controller.materialise_provisioner_sources()` (D3) and rewiring the step loop

- `src/strata/controllers/build_controller.py`: add the recursive, memoized
  helper (D3's code above) near `find_provisioner()`/`sync_source` usage.
- In `build_run()`'s step loop: seed `materialised: dict[str, Path] = {}`
  once before `for step in ordered_by_depends_on(...)`; replace the
  existing direct `sync_source(context.root, build_path, provisioner.source,
  remotes)` call with `materialise_provisioner_sources(context, build_path,
  remotes, workspace, provisioner, materialised, dry_run=dry_run,
  on_step=on_step)` (this call recurses into `provisioner.depends_on`
  first, then materialises `provisioner` itself — same end state the old
  call produced for a provisioner with no dependencies, so this is a
  superset change, not a behaviour break for existing workspaces).
- Dependency-only provisioners are **not** looped over separately and are
  **never** passed to `resolve_integration()`/`integration.prepare()` —
  they only ever appear as a side effect of some step's own provisioner
  recursing into its `depends_on`.
- `--dry-run` messaging: dependency provisioners get
  `"would materialise provisioner '<name>' source (dependency)"`
  (distinguishable from a step's own
  `"would materialise provisioner '<step.name>' source (...)"` message).
- New tests in `tests/strata/controllers/test_build_controller.py`:
  - `test_build_run_materialises_a_dependency_provisioner_with_no_execution_step`
    — the issue's own shape (`spoke_infra` depends on `iac_components`/
    `iac_primitives`, neither has a step); assert both dependency
    directories exist on disk after `build_run()`.
  - `test_build_run_materialises_a_shared_dependency_only_once` — two
    steps whose provisioners both depend on the same staging provisioner;
    assert `sync_source`/the filesystem copy only happens once (patch/spy
    on `sync_source` or assert via a call counter).
  - `test_build_run_materialises_a_transitive_dependency_chain` — A depends
    on B depends on C, none but A has a step; assert all three land on
    disk.
  - `test_build_run_dry_run_reports_dependency_materialisation` — asserts
    the distinct dependency-only message via `on_step`.
  - `test_build_run_never_resolves_integration_for_a_dependency_only_provisioner`
    — asserts `resolve_integration()`/`integration.prepare()` is not
    invoked for a pure-dependency provisioner (spy/mock).

### Phase 5 — docs

- Update [build-command.md](build-command.md)'s pseudocode and "what
  already exists" status table with the new
  `materialise_provisioner_sources()` row once Phase 4 lands.
- Update [build-pipeline-status.md](build-pipeline-status.md) if its own
  status table still lists this as open.
- Graduate this doc to `docs/design/` per the normal
  [docs/work/README.md](README.md) life cycle once nothing above is
  pending — collapse this Implementation Plan and the Changelog down into
  one trailing `## History` section at that point, per that README's own
  rule.

## Related Decisions

- [ADR-0029](../decisions/0029-provisioner-source-dependencies.md) — the
  accepted decision this doc now only tracks implementation for (D1-D4,
  the alternatives comparison, and Consequences all live there, not here).
- [ADR-0022](../decisions/0022-strata-build-run.md) D3 — found the
  adjacent "destination mirrors `source_path`" half of this same gap
  (confirmed already built); this document covers the other half (whether
  materialisation is triggered at all for a provisioner no step
  references).
- [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md) —
  the provisioner/topology decoupling precedent this proposal's "orthogonal
  facts" framing (Open Questions, second bullet) follows.
- [ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md) —
  why `requires` was rejected as this field's name (D1).
- [build-command.md](build-command.md) / [build-pipeline-status.md](build-pipeline-status.md) —
  current `build run` status this feature slots into.

## Changelog

- 2026-10-06: Created from a real IaC team report — root cause confirmed
  directly against `build_controller.py`/`workspace_model.py`/
  `provisioning_model.py`.
- 2026-10-06: Evaluated 3 alternatives against `depends_on` (multiple
  provisioners per execution step in two shapes, and a `deployable: bool`
  classification flag with unconditional sync) — all recorded under
  Alternatives Considered. Also tightened D3's wording: it was already a
  plain reachability DFS, not a full topological sort — made explicit that
  sibling source copies have no ordering requirement, only a reachability
  one, since they don't cross-read each other during materialisation.
- 2026-10-06: Decision finalised as [ADR-0029](../decisions/0029-provisioner-source-dependencies.md)
  (`depends_on` on `ProvisionerModel`, over options B/C/D). This doc's
  Status moved to in-progress/implementation-tracking only; the Decision
  Outcome/Consequences content now lives solely in the ADR, not duplicated
  here. Implementation Plan expanded into 5 concrete, file-by-file phases
  with real test names.
