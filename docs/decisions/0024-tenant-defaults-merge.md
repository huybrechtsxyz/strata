# Tenant Defaults Merge — `properties`/`custom`/`environments` as a Base Layer

- Status: implemented
- Date: 2026-09-24
- Related: [ADR-0023](0023-build-output-rendering.md) (`tenant.spec.configuration`
  is deliberately **not** part of this ADR - its real destination is
  Terraform's still-deferred `tenant` category, Phase 2c there, not a
  deployment-spec merge)

## Context and Problem Statement

`TenantSpecModel` (`tenant_model.py`) already declares the full contract this
ADR implements - the model was written ahead of the code:

```python
environments: ... = Field(
    None, description="Names of Environment documents merged in BEFORE a deployment's own environments, so "
    "deployment values win. ..."
)
properties: dict[str, Any] | None = Field(
    None, description="Tenant-wide deployment properties, merged as a base layer into spec.properties of every "
    "deployment referencing this tenant. Deployment values take precedence.",
)
custom: dict[str, Any] | None = Field(
    None, description="Tenant-wide custom data merged as a base layer into spec.custom of every deployment "
    "referencing this tenant. Deployment values take precedence.",
)
```

Checked which half is actually real: `environments` **is** wired up -
`value_controller.resolve_values()` already extends tenant environments
before the deployment's own (`environment_names.extend(tenant.spec.environments
or []); environment_names.extend(deployment.spec.environments or [])`), and
`merge_environment_models()`'s later-wins semantics make the deployment's
own environment take precedence, exactly as documented.

`properties`/`custom` are **not** wired up anywhere - grepped the whole
codebase for `tenant.spec.properties`/`tenant.spec.custom`/`.configuration`
and found zero references outside the field declaration itself. The
docstring's contract was never implemented.

## Decision

**D1: reuse `merge_deployment_specs()` - do not invent a second merge
mechanism.** That function already implements exactly the semantics needed
(`deployment_service.py`, already tested, already used for `extends`):
deep-merge per leaf key for `properties`/`custom` (child/deployment wins on
a key conflict, keeps the other's untouched keys - not a whole-value
replace), and `environments`: base list first, then child's, matching the
already-correct precedence `value_controller.py` implements by hand today.
Build a small synthetic "base" dict carrying only the three fields meant to
flow through, not tenant's whole spec (`display_name`/`geographies`/
`onboarded` are tenant-identity fields with no `DeploymentSpecModel`
equivalent - merging them in would either be silently dropped or a
validation error, neither desirable):

```python
tenant_base = {
    "environments": tenant.spec.environments or [],
    "properties": tenant.spec.properties or {},
    "custom": tenant.spec.custom or {},
}
merged_dict = merge_deployment_specs(tenant_base, spec_dict)
```

**D2: applied inside `resolve_deployment_chains()`, after `extends`
resolution, not before.** A deployment's `tenant` reference itself
participates in normal `extends` merging (it is just another
`DeploymentSpecModel` field) - by the time `_resolve_dict()` produces the
fully-merged `extends` chain, `merged_dict["tenant"]` already holds the
correct, final tenant name for that deployment, however many `extends`
links deep it came from. Tenant is even more "base" than the deployment's
own `extends` root, so it merges in as one further outer layer, underneath
the already-fully-resolved dict - never the other way around, and never
interleaved with individual `extends` links:

```python
def _merge_tenant_defaults(spec_dict: dict[str, Any], index: DocumentIndex) -> dict[str, Any]:
    """Fold a tenant's properties/custom/environments in as a base layer
    under the deployment's own - deployment always wins on key conflicts.
    A missing/unresolvable tenant reference silently no-ops (validate_references
    already reports a bad spec.tenant separately) - same treatment _resolve_dict()
    already gives a missing extends ancestor.
    """
    tenant_name = spec_dict.get("tenant")
    if not tenant_name:
        return spec_dict
    tenant_entry = index.get(PlatformKind.TENANT, tenant_name)
    if tenant_entry is None:
        return spec_dict
    tenant = cast(TenantModel, tenant_entry.model)
    tenant_base = {
        "environments": tenant.spec.environments or [],
        "properties": tenant.spec.properties or {},
        "custom": tenant.spec.custom or {},
    }
    return merge_deployment_specs(tenant_base, spec_dict)
```

Called once per deployment in `resolve_deployment_chains()`'s existing loop,
right before `DeploymentModel.model_validate(...)` - the same point
`merged_dict` is already fully assembled and about to become the function's
returned, canonical "complete" deployment.

**D3: remove `value_controller.py`'s own tenant-environment-prepending code
- one mechanism, not two independently-drifting ones.** Once
`resolve_deployment_chains()` folds tenant environments in, `resolve_values()`'s
`deployment` (already pulled from `resolved_deployments`) has them already -
its own hand-rolled lookup becomes redundant, and if left in place would
double-prepend tenant environments for any deployment present in both
`resolved_deployments` and reachable via the old code path. Simplifies to:

```python
environment_names: list[str] = list(deployment.spec.environments or [])
```

removing the `if deployment.spec.tenant: tenant_entry = index.get(...)` block
entirely - this is the one real behaviour change existing tests must not
regress on (see Implementation Plan).

**D4: `tenant.spec.configuration` stays untouched by this ADR.** Its own
docstring is explicit that it is different in kind - *"emitted verbatim to
the provisioner... NOT merged into deployment properties, unlike `properties`
above"* - it has no `DeploymentSpecModel` field to merge into at all. Its
real consumer is Terraform's deferred `tenant` category (ADR-0023 Phase 2c),
which already has zero fixture data to ground it against; nothing here
changes that.

## Consequences

- Good: closes a real gap between a model's documented contract and actual
  behaviour, found by checking real code rather than trusting the docstring.
- Good: zero new merge logic - `merge_deployment_specs()` is reused exactly
  as-is, already tested for the deep-merge-per-leaf-key and
  environments-base-then-child semantics this needs.
- Good: collapses two independently-drifting implementations of the same
  "tenant environments come first" rule into one.
- Neutral: this is a `strata.controllers`-layer fix, not scoped to any one
  consumer - every future reader of `resolve_deployment_chains()`'s output
  (not just `value_controller.py`) gets tenant defaults for free, including
  ADR-0022/0023's `build_run()` once it exists.
- Bad: `resolve_deployment_chains()`'s own docstring is scoped to "`extends`
  chain resolution" - this ADR gives it a second responsibility (tenant
  defaults). Considered a small dedicated function called separately by
  every consumer instead, rejected: every current and planned consumer
  wants the same fully-resolved deployment, and a second call site is one
  more place to forget to call it, the same "one mechanism, not two"
  argument as D3.

## Implementation Plan

Single phase - small, contained, one existing test file to extend plus one
existing call site to simplify.

- `deployment_resolution.py`: add `_merge_tenant_defaults()`, call it in
  `resolve_deployment_chains()`'s loop right before
  `DeploymentModel.model_validate(...)`.
- `value_controller.py`: remove the now-redundant tenant lookup in
  `resolve_values()`, per D3.
- Tests (`test_deployment_resolution.py`, matching its existing
  `_deployment()`/`_index()` fixture helpers - needs a `_tenant()` helper
  and `PlatformKind.TENANT` index entries added alongside):
  - A deployment referencing a tenant with `properties`/`custom` gets them
    merged in; deployment's own values for the same keys win.
  - A deployment referencing a tenant with `environments` gets them
    prepended before its own.
  - A deployment with no `tenant` reference is unaffected (no tenant
    lookup attempted).
  - A deployment referencing an unresolvable tenant name is unaffected
    (silent no-op, matching a missing `extends` ancestor's treatment) -
    `validate_references` is the layer that reports a bad `spec.tenant`.
  - Tenant merging happens *after* `extends` resolution: a deployment that
    only gets its `tenant` reference via an `extends` base still has tenant
    defaults folded in correctly.
- `test_value_controller.py`: update/remove whatever test currently exercises
  the old tenant-environment-prepending code path there, replacing it with
  an assertion that `resolve_values()` still sees tenant environments -
  now via `resolve_deployment_chains()`'s output rather than its own lookup.

**Done when:** all of the above tests pass, plus the full check suite
(mypy/ruff/import-linter/pytest) stays green with no regression in existing
`extends`-chain or `resolve_values()` tests.

**Implemented.** `_merge_tenant_defaults()` added to `deployment_resolution.py`,
called once per resolved deployment right before final `DeploymentModel`
validation. `value_controller.py`'s own tenant-environment lookup removed
per D3 (now redundant `TenantModel` import removed too). 5 new tests in
`test_deployment_resolution.py` (18 total); the pre-existing end-to-end
`test_deployment_environment_overrides_tenant_environment`
(`test_value_controller.py`) passes unchanged, confirming the merge point
moved without changing observable behaviour. Full check suite green:
880/880 tests passing.
