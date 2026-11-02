# Provisioner Source Dependencies — Materialising a Source With No Execution Step

- Status: accepted
- Date: 2026-10-06
- Related: [ADR-0022](0022-strata-build-run.md) D3 (found the adjacent
  "destination mirrors `source_path`" half of this same gap, already built;
  this decision closes the other half), [ADR-0011](0011-topology-and-provisioning-decoupling.md)
  (the provisioner/topology decoupling precedent this decision's "orthogonal
  facts" reasoning follows), [ADR-0002](0002-requirement-interface-injection-grant-lessons-from-v1.md)
  (why `requires` was rejected as this field's name)
- See also: [docs/design/provisioner-source-dependencies.md](../design/provisioner-source-dependencies.md)
  for how the mechanism actually works, now that it's fully built —
  not duplicated here

## Context and Problem Statement

A real IaC team's pipeline hit a genuine Terraform module-resolution
failure (`Unable to evaluate directory symlink: lstat ../../components: no
such file or directory`) the first time a real `terraform init` ever ran
against `build run` output — previously masked, since `deploy run
--dry-run` never touches Terraform at all.

Root cause, confirmed directly against
[`build_controller.py`](../../src/strata/controllers/build_controller.py):
`build_run()`'s only materialisation loop iterates
`workspace.spec.execution`, never `workspace.spec.provisioners` directly:

```python
for step in ordered_by_depends_on(workspace.spec.execution or []):
    provisioner = find_provisioner(workspace, step.provisioner)
    ...
    source_path = sync_source(context.root, build_path, provisioner.source, remotes)
```

A `ProvisionerModel` declared with no `ProvisioningStepModel` ever naming
it — confirmed empirically: a `source_path`-only provisioner meant purely
to stage a shared Terraform module library so a sibling provisioner's own
`.tf` files can resolve a relative `source = "../../components/x"` path —
is never passed to `sync_source()` at all, and produces zero output
directory. `WorkspaceSpecModel` has no separate staging/sync concept
either (only `providers`/`provisioners`/`execution`/`topology`/
`resources`), so there was no way to declare "materialise this source, but
it's not a deployable step."

This is the other half of a gap [ADR-0022](0022-strata-build-run.md) D3
already found but did not close. D3 fixed *where* a materialised source
lands (destination mirrors the source's own `source_path`, so sibling
relative-path composition resolves) but never addressed *whether* a
provisioner with no execution step gets materialised at all.

The workaround shipped in the meantime — giving each staging provisioner a
real execution step with a `scope: stage` label and a borrowed `targets:`
entry (forced by `ProvisioningStepModel.targets`'s `min_length=1`), relying
on every real `deploy run` invocation always passing `--scope infra` —
works, but leaves these non-deployable provisioners reachable as steps,
with nothing stopping a future invocation from forgetting `--scope infra`
and planning/applying one for real.

## Considered Options

Full comparison, including code sketches and a criteria-by-criteria
breakdown, is in
[docs/design/provisioner-source-dependencies.md](../design/provisioner-source-dependencies.md)'s
`## History` section. Summarised:

- **A — `ProvisionerModel.depends_on: list[PlatformName]`.** The dependency
  is declared on the tool definition that actually needs it — a sibling
  provisioner's source must be materialised first. Composes transitively
  for free (a dependency can itself have dependencies) via recursion;
  shared dependencies cost nothing extra (each consumer just names it,
  a memoized sync set dedupes the actual work).
- **B — `ProvisioningStepModel.provisioner` becomes `provisioners:
  list[PlatformName]`.** Rejected: requires an implicit or explicit
  convention for which list entry is the one actually planned/applied
  ("first wins" is a reordering footgun; an explicit per-entry role marker
  just reinvents option A one level deeper, inside a model whose job is
  execution order, not source composition). Does not remove duplication
  when one staging provisioner feeds multiple consumers.
- **C — additive `ProvisioningStepModel.materialise: list[PlatformName]`,**
  keeping `provisioner:` singular. Fixes B's ordering ambiguity, but still
  duplicates per consuming step and cannot express a transitive staging
  chain (no step exists for a provisioner that itself has no execution
  step, so there's nowhere to attach its own dependency list).
- **D — `ProvisionerModel.deployable: bool = True` classification flag,**
  with `build_run()` unconditionally syncing every non-deployable
  provisioner, no dependency graph at all. The strongest competitor to A:
  no cycle detection, no recursion, transitive chains handled for free
  (every provisioner in the chain just gets the flag). Trade-off: loses
  the explicit "`spoke_infra` needs exactly these two" documentation link;
  syncs a declared-but-currently-unused staging provisioner unconditionally
  every build (harmless, just not lazy).

## Decision Outcome

Chosen: **Option A — `ProvisionerModel.depends_on: list[PlatformName] |
None`**, validated and ordered the same way
`ProvisioningStepModel.depends_on` already is (unknown-name + cycle check
at Phase 1; a recursive, memoized walk at build time — see the work doc's
D1-D3 for the full mechanism), with one deliberate simplification found
while designing it: **materialisation order among sibling dependencies
never actually matters** (they are independent file copies into separate
directories with no cross-reads at sync time — only whether each gets
synced at all does), so the build-time walk is a plain reachability DFS,
not a full topological sort. Phase-1 cycle rejection still runs (reusing
the same Kahn's-algorithm shape `validate_provisioning_steps()` already
uses for steps, extracted into one shared utility), but it is a defensive
sanity check against a near-certain documentation mistake, not something
the build-time walk itself depends on for correctness.

**Why A over D:** given how much this repo's real consumers rely on
workspace YAML being self-documenting
(`/memories/repo/v1-consumer-usage.md`), the explicit relationship an
`iac_components`/`iac_primitives` consumer declares in its own
`depends_on` is judged worth the small added implementation cost over D's
flat classification flag — one field, one validator, one recursive helper,
not a large delta over D's flat loop. D is recorded as the fallback to
reconsider if real usage shows dependency chains never exceed one level and
the "why" is always obvious from naming alone.

**Naming: `depends_on`, not `requires`/`stage_with`.** Reuses the exact
word `ProvisioningStepModel.depends_on` already uses for an analogous
"these other named things must be processed first" meaning — the host
model differs (`provisioners[].depends_on` names other provisioners;
`execution[].depends_on` names other steps), and YAML's own nesting already
disambiguates which graph a given `depends_on` walks, so a second word for
the same concept would cost more reader clarity than it buys. `requires`
was considered and rejected specifically because ADR-0002 already
relegated a similarly-named idea (a `spec.references`-style declared
requirement field) to "rejected" — reusing that word for an unrelated
mechanism risks a reader conflating the two.

**A dependency-only provisioner is never passed to `resolve_integration()`/
`integration.prepare()`.** It is sync-only by construction — nothing
renders a `.tfvars.json`/`values.yaml` for it, and it never needs an
`Integration` binding, matching the issue's own framing ("these aren't
deployable steps, they're materialisation dependencies").

### Consequences

- Good: a provisioner with no execution step is no longer silently
  unmaterialised — the actual bug reported is fixed.
- Good: removes the "forgot `--scope infra`" footgun entirely — a
  dependency-only provisioner is never reachable as a step at all, so no
  `--scope`/`--stage` filter can accidentally include or exclude it.
- Good: no more borrowed/meaningless `targets:` entry forced by
  `ProvisioningStepModel.targets`'s `min_length=1` — a dependency
  provisioner declares no step, so that constraint never applies to it.
- Good: transitive staging chains (a dependency with its own dependency)
  compose for free via recursion, with no new schema surface beyond the
  one field.
- Good: reuses the existing Kahn's-algorithm cycle-check shape
  (`validate_provisioning_steps()`), extracted once into a shared utility
  rather than duplicated a third time.
- Neutral: adds one more `depends_on`-shaped field to the schema, distinct
  from but easily confused at a skim with `ProvisioningStepModel.depends_on`
  if a reader doesn't notice which model they're looking at — mitigated by
  each field's own docstring cross-referencing the other.
- Bad: a provisioner that is a pure materialisation dependency with no
  consumer anywhere still never gets synced (declared-but-unreferenced is
  indistinguishable from "intentionally unused" under this design) — judged
  acceptable, since nothing needs an unreferenced source materialised by
  definition; flagged in the work doc's Open Questions as not restricted
  further for lack of evidence it's needed.
