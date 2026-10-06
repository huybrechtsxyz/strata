# Provisioner Source Dependencies — Materialising a Source With No Execution Step

- Status: built and end-to-end tested — every code excerpt below is real,
  shipped code (`src/strata/utils/dependency_order.py`,
  `src/strata/models/provisioning_model.py`,
  `src/strata/models/workspace_model.py`,
  `src/strata/controllers/build_controller.py`), not a sketch.
- Last updated: 2026-10-06

## Overview

`build_run()`'s materialisation loop only ever iterated
`workspace.spec.execution`, never `workspace.spec.provisioners` directly —
a `ProvisionerModel` declared with no `ProvisioningStepModel` referencing
it was never synced into `build_path` at all. This surfaced as a real
Terraform module-resolution failure (`Unable to evaluate directory
symlink: lstat ../../components: no such file or directory`) the first
time a real `terraform init` ran against `build run` output in a
production pipeline — a `source_path`-only provisioner meant purely to
stage a shared Terraform module library (so a sibling provisioner's own
`.tf` files could resolve a relative `source = "../../components/x"` path)
produced zero output directory.

This is the other half of a gap
[ADR-0022](../decisions/0022-strata-build-run.md) D3 found but did not
close. D3 fixed *where* a materialised source lands
([source_sync.py](../../src/strata/controllers/source_sync.py)'s
destination mirrors each source's own `source_path`, so sibling
relative-path composition resolves) but never addressed *whether* a
provisioner with no execution step gets materialised at all.

The workaround used in the meantime — giving each staging provisioner a
real execution step with a `scope: stage` label and a borrowed `targets:`
entry (forced by `ProvisioningStepModel.targets`'s `min_length=1`), relying
on every real `deploy run` invocation always passing `--scope infra` —
worked, but left these non-deployable provisioners reachable as steps,
with nothing stopping a future invocation from forgetting `--scope infra`
and planning/applying one for real.

[ADR-0029](../decisions/0029-provisioner-source-dependencies.md) is the
decision record for the fix below (`ProvisionerModel.depends_on`, chosen
over 3 other options compared there); this document describes how it
actually works, now that it's built.

## Current Design

### `ProvisionerModel.depends_on`

```python
depends_on: list[PlatformName] | None = Field(
    None,
    description="Names of other Provisioners in this same workspace whose 'source' must be materialised "
    "before this one's, independent of 'execution'/'targets' (ADR-0029). A dependency provisioner named "
    "here does not need its own execution step — it is synced purely to make its files available on disk "
    "for this provisioner's own source code to compose (e.g. a relative Terraform module path) at build "
    "time. Never implies an execution order or deploy-time relationship by itself — see "
    "ProvisioningStepModel.depends_on for that, which names steps, not provisioners.",
)

@model_validator(mode="after")
def validate_no_self_dependency(self) -> "ProvisionerModel":
    if self.depends_on and self.name in self.depends_on:
        raise ValueError(f"Provisioner '{self.name}' cannot depend on itself.")
    return self
```

Reuses the word `depends_on` from `ProvisioningStepModel.depends_on`
deliberately, rather than `requires`/`stage_with` — the *meaning* is
identical ("these other named things must be processed first"), only the
namespace differs (provisioner names here vs. step names there), and
YAML's own nesting already disambiguates which graph a given `depends_on`
walks. `requires` was rejected specifically because
[ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md)
already relegated a similarly-named idea (a `spec.references`-style
declared requirement field) to "rejected" — reusing that word risks a
reader conflating the two unrelated mechanisms.

### `WorkspaceSpecModel.validate_provisioner_depends_on()` — cross-document validation

A lone `ProvisionerModel` has no sibling awareness, so existence and cycle
checking live on `WorkspaceSpecModel` instead — the same two-pass shape
`validate_execution()` already uses for `ProvisioningStepModel.depends_on`
(unknown names first, then a cycle check), over a completely independent
graph (provisioner names, not step names):

```python
@model_validator(mode="after")
def validate_provisioner_depends_on(self) -> "WorkspaceSpecModel":
    names = {p.name for p in self.provisioners}
    errors = []
    for provisioner in self.provisioners:
        for dep in provisioner.depends_on or []:
            if dep not in names:
                errors.append(
                    f"Provisioner '{provisioner.name}': depends_on '{dep}' is not a declared provisioner."
                )
    if errors:
        raise ValueError("; ".join(errors))

    topological_order(
        names, {p.name: p.depends_on or [] for p in self.provisioners}, label="provisioner depends_on"
    )
    return self
```

A provisioner with **no** execution step at all validates cleanly here —
that's the entire point: `iac_components`/`iac_primitives`-style staging
provisioners need never appear in `execution`.

### `strata.utils.dependency_order.topological_order()` — one shared Kahn's-algorithm implementation

Three call sites share this one function instead of a third/fourth copy of
the same ~15-line block:

```python
def topological_order(
    names: Iterable[str], depends_on: Mapping[str, Sequence[str]], *, label: str = "items"
) -> list[str]:
    """Kahn's algorithm. Returns `names` ordered so every dependency listed
    in `depends_on[name]` precedes `name` in the result ... """
```

- `provisioning_model.validate_provisioning_steps()` — cycle check only
  (`label="provisioning step depends_on"`), order discarded.
- `build_controller.ordered_by_depends_on()` — the real execution order
  (`label="execution steps"`); raises on a cycle instead of its old
  behaviour of silently dropping cyclic steps from the result (never
  observable on real input — this is only ever called on input
  `validate_provisioning_steps()` already proved acyclic).
- `validate_provisioner_depends_on()` above (`label="provisioner
  depends_on"`) — cycle check only, order discarded (materialisation order
  among provisioners is computed separately below, and doesn't use this
  function at all — see why in the next section).

`label` exists purely so each call site's error message stays in its own
vocabulary (e.g. `"Circular dependency in provisioner depends_on: a -> b"`)
without this function knowing what kind of graph it was given.

### `build_controller.materialise_provisioner_sources()` — the actual fix

```python
def materialise_provisioner_sources(
    context: SolutionContext,
    build_path: Path,
    remotes: dict[str, SolutionRemoteModel],
    workspace: WorkspaceModel,
    provisioner: ProvisionerModel,
    materialised: dict[str, Path | None],
    *,
    dry_run: bool = False,
    on_step: Callable[[str], None] | None = None,
) -> Path | None:
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
                on_step(
                    f"would materialise provisioner '{provisioner.name}' source "
                    f"(dependency, {describe_source(provisioner.source)})"
                )
        else:
            source_path = sync_source(context.root, build_path, provisioner.source, remotes)
            if on_step:
                on_step(f"materialised provisioner '{provisioner.name}' source at {source_path} (dependency)")
    materialised[provisioner.name] = source_path
    return source_path
```

**Plain reachability DFS, deliberately not `topological_order()`.** Sibling
sources are independent file copies into separate directories with no
cross-reads at sync time — the *order* two unrelated dependencies are
copied in never matters, only whether each gets synced at all does.
`validate_provisioner_depends_on()` already guarantees the graph is
acyclic, so this recursion never needs to detect a cycle itself.

**Wired into `build_run()`'s step loop as an addition, not a replacement:**
one `materialised: dict[str, Path | None] = {}` is seeded once before
`for step in ordered_by_depends_on(workspace.spec.execution or [])`; for
each step, its own provisioner's `depends_on` entries are recursively
materialised *before* the step loop's pre-existing per-step code
(`resolve_integration()`, `sync_source()` for the step's own provisioner,
`integration.prepare()`) runs, which is otherwise completely untouched —
including the `provisioner.source is None` → `build_path / step.name`
GitOps destination convention, which has nothing to do with `depends_on`
and would have been risky to fold into one combined function.

**A dependency-only provisioner is never passed to `resolve_integration()`/
`integration.prepare()`** — it is sync-only by construction. Nothing
renders a `.tfvars.json`/`values.yaml` for it, and it never needs an
`Integration` binding.

**`materialised` is shared across the whole `build_run()` invocation**, so
a dependency provisioner named by more than one consumer (two different
deployable provisioners both depending on the same staging provisioner) is
synced exactly once, not once per consumer — confirmed under both a real
run and `--dry-run` (the dry-run path stores `None` rather than a real
`Path`, a genuinely different code path, covered by its own test).

### Migration example

Before (the shipped workaround):

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

After:

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
    - name: iac_primitives             # no execution step at all
      tool: terraform
      source: {...}
  execution:
    - name: spoke_infra_step
      provisioner: spoke_infra
      targets: [spoke_network]
```

`iac_components`/`iac_primitives` are no longer reachable as steps at all —
`--scope infra`/`--scope stage` filtering has nothing to accidentally
include or exclude them from, removing the "forgot `--scope infra`"
footgun entirely.

## Deliberately out of scope

- **A `depends_on` target with no `source` (a sync/GitOps provisioner) is
  permitted as a no-op**, not rejected — mirrors
  `ProvisionerModel.validate_source_required_unless_sync()`'s existing
  leniency elsewhere on this same model. Revisit only if a real workspace
  surfaces a case where this silent no-op is surprising.
- **`depends_on` is unrestricted** — a provisioner may be both a
  `depends_on` target *and* the subject of its own execution step. That
  shape gets synced twice (once via the dependency walk, once by the step
  loop's own call), which is harmless (`sync_source()` is an idempotent
  copy) but not deduplicated against the step loop's own call. A
  provisioner's execution-step membership and its `depends_on`
  materialisation needs are orthogonal facts, matching
  [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md)'s
  "topology and provisioning are decoupled" precedent elsewhere in this
  model family.
- **`strata graph` does not show a `depends_on` edge for this field** —
  it only walks `References()`-annotated cross-document fields;
  `ProvisionerModel.depends_on` is a same-document, same-list name
  reference, the same category `ProvisioningStepModel.provisioner`/
  `.depends_on` already are, neither of which appears in `strata graph`
  output today either.
- **A `depends_on`-only provisioner that also sets
  `backend`/`output`/`integration` has those fields silently unused** —
  structurally the same bug *class* v1's own ADR-0071 already fixed once
  (a field validated successfully but silently ignored for a known-wrong
  provisioner *type*), just triggered by "never reachable as a step"
  instead of "wrong tool type." Not generically checkable here: "never
  named by any step" is a workspace-wide data-flow fact, not a per-field
  schema fact, and (per the bullet above) a provisioner can legitimately be
  both a `depends_on` target and the subject of its own step — so "has
  `depends_on` consumers" alone doesn't even imply "fields are unused."
  Revisit only if a real workspace is found setting one of these fields on
  a provisioner that truly never deploys.

## Related Decisions

- [ADR-0029](../decisions/0029-provisioner-source-dependencies.md) — the
  decision record (why `depends_on` over 3 other options: multiple
  provisioners per execution step in two shapes, and a `deployable: bool`
  classification flag with unconditional sync).
- [ADR-0022](../decisions/0022-strata-build-run.md) D3 — found the
  adjacent "destination mirrors `source_path`" half of this same gap,
  already built before this feature; this feature closes the other half.
- [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md) —
  the provisioner/topology decoupling precedent the "orthogonal facts"
  reasoning above follows.
- [ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md) —
  why `requires` was rejected as this field's name.
- [build-command.md](build-command.md) / [build-pipeline-status.md](build-pipeline-status.md) —
  current `build run` status this feature slots into.

## History

- Considered and rejected letting one execution step bind multiple
  provisioners (`provisioner: str` → `provisioners: list[str]`, or an
  additive `materialise: list[str]` alongside it): both require either an
  implicit "first entry is the deployable one" convention (a YAML-reorder
  footgun) or an explicit per-entry role marker that just reinvents
  `depends_on` one level deeper, and neither removes the duplication when
  one staging provisioner feeds several consumers. A separate top-level
  `staging:`/`sync:` list was also rejected — it would duplicate
  `ProvisionerModel`'s whole shape for an entry that's structurally the
  same kind of thing, just never bound to an execution step.
- A `ProvisionerModel.deployable: bool` classification flag (sync every
  non-deployable provisioner unconditionally, no dependency graph at all)
  was the strongest competitor found — no cycle detection, no recursion,
  free transitive chains. Not chosen: it drops the explicit "`spoke_infra`
  needs exactly these two" documentation link a real workspace YAML
  benefits from, in exchange for a small implementation-size win. Worth
  reconsidering if real usage ever shows `depends_on` chains never
  meaningfully exceed one level.
- Materialisation order among sibling dependency sources never actually
  matters — they're independent file copies into separate directories with
  no cross-reads at sync time, so `materialise_provisioner_sources()` is a
  plain reachability DFS, not a full topological sort (contrast
  `ordered_by_depends_on()`, which orders *execution* steps, where real
  ordering does matter and a full sort is the right tool).
- `build_controller.ordered_by_depends_on()` used to silently drop cyclic
  steps from its result instead of raising — an accidental behaviour
  (its own docstring's "would hang" claim was never actually accurate,
  it just returned a truncated list) fixed for free once it became a thin
  wrapper over `topological_order()`. Never observable on real input,
  since this function is only ever called on input already proven acyclic.
- A code review pass across all 4 implementation phases found 3 real gaps
  (now fixed): the dependency dry-run message was missing the
  `describe_source()` detail the step-level one already had;
  `ordered_by_depends_on()`'s new raise-on-cycle behaviour had no
  regression test at that specific call site; and the shared-dependency-
  synced-once guarantee had no dry-run equivalent test (a genuinely
  different code path — `None` is stored instead of a real `Path`).
