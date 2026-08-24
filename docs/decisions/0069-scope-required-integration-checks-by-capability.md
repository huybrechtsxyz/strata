# Scope Required-Integration Checks by Command/Capability

- Status: deferred
- Date: 2026-08-24

## Context and Problem Statement

Reported by the haven team: an integration declared `required: true` in
`spec.integrations` (e.g. a `terraform`-capability entry) affected commands that
have nothing to do with that capability — e.g. `strata values get`, which only
needs secret/variable/feature store integrations, not Terraform. Haven's
workaround was to set `required: false` on the Terraform integration.

### Where this lives today

`IntegrationService.validate_required_integrations()`
([src/strata/services/integration_service.py](../../src/strata/services/integration_service.py#L161))
iterates **every** `spec.integrations[]` entry with `required: true` in the
merged configuration and checks `is_integration_registered()` /
`is_integration_available()` for each — with no notion of which capability the
*invoking command* actually needs. It is called unconditionally from the end of
`initialize_integrations()`, which itself is triggered lazily by whichever
controller first needs the integration registry (identity, cost, audit, value
resolution, `sln doctor`, ...).

This is a different, blunter mechanism than the one commands already use to
declare their own needs: `BaseCommand.get_required_integrations()`
([src/strata/commands/base_command.py](../../src/strata/commands/base_command.py#L194))
returns a `{integration_name: operation_description}` dict that each command
subclass opts into (e.g. `strata new`, workitem commands, audit export), and is
checked per-command in `_validate_requirements()` — already correctly scoped.
`ValueController` also already has a properly-scoped, capability-aware check:
`_preflight_check_stores()`
([src/strata/controllers/value_controller.py](../../src/strata/controllers/value_controller.py#L613))
only calls `ensure_available()` for store types actually referenced by the
deployment's declared variables/secrets/features.

**Current mitigation already in place:** `ValueController._ensure_integrations_initialized()`
([src/strata/controllers/value_controller.py](../../src/strata/controllers/value_controller.py#L665))
treats a failed `initialize_integrations()` result as a logged warning, not a
hard failure — so today, a `required: true` Terraform integration does *not*
actually abort `values get` in this codebase. The blunt, config-global
semantics of `validate_required_integrations()` remain, though: nothing in its
contract says "only matters if the caller cares about this capability," so the
next caller that decides to propagate `initialize_integrations()`'s `ok` value
as fatal (a reasonable-looking change in isolation) would silently reintroduce
exactly the failure haven hit. Not urgent, not a regression — but worth closing
the gap in the contract itself rather than relying on every future call site
independently remembering to downgrade it to a warning.

### Related gap: `is_available()` is PATH-only

`BaseIntegration.is_available()`
([src/strata/integrations/base_integration.py](../../src/strata/integrations/base_integration.py#L403))
determines availability solely by running `get_version_command()` (e.g.
`git --version`) and checking the exit code — i.e. "is the binary resolvable
on `PATH`". There is currently no fallback that also recognizes an integration
as available via an environment variable (e.g. an auth-token/endpoint env var
indicating the integration is reachable through an API rather than a local
CLI, or an env var pointing at a non-`PATH` executable location). This is a
narrower, related blind spot to the scoping problem above: even once a
`required: true` check is correctly scoped to the capability a command needs
(Option B), it can still misreport "not available" for an integration whose
presence is legitimately signaled by an env var instead of a `PATH` lookup.
Confirmed real-world instance: `Dockerfile.cli` (`python:3.13-slim`) doesn't
install `git`, so any command that reaches `initialize_integrations()` inside
that image would fail the `git` `required: true` check today, regardless of
capability-scoping — a PATH-and/or-env-var-aware `is_available()` would let
such environments signal availability without installing the CLI. Should be
addressed alongside the capability-filter work (Option B) rather than as a
separate, uncoordinated change, since both touch the same availability
contract.

## Considered Options

- **A. Status quo.** Keep `validate_required_integrations()` global/config-wide.
  Commands that need a hard failure already have the correctly-scoped
  `get_required_integrations()` (per-integration-name) and
  `_preflight_check_stores()` (per-store-capability) mechanisms available;
  document that the global check is advisory only (used for `sln doctor`-style
  reporting) and must never be treated as fatal by a new call site.
- **B. Add an optional capability filter to `validate_required_integrations()`.**
  e.g. `validate_required_integrations(capabilities: Optional[Set[Type]] = None)`
  — when provided, only `required: true` specs whose `capabilities` intersect
  the given set are checked; `None` preserves today's global behavior (used by
  `sln doctor`, which legitimately wants to see everything). Callers like
  `ValueController` would pass the concrete store capability protocols
  (`ISecretStore`, `IVariableStore`, `IFeatureStore`) instead of relying on the
  warning-only workaround.
- **C. Deprecate the global check entirely** and require every enforcement path
  to go through per-command (`get_required_integrations()`) or per-capability
  (`_preflight_check_stores()`-style) checks. Loses the single "does this
  config's required integrations all check out" view `sln doctor` wants.

## Decision Outcome

Deferred — not urgent (no current hard failure), not a regression. When
picked up, **Option B** is the recommended direction: it keeps one config-wide
sanity view for `sln doctor`/`strata tools status` while letting scoped callers
(`ValueController`, future controllers) ask "are only the integrations *I* need
okay?" instead of relying on downstream code to swallow an unrelated failure as
a warning. The `is_available()` PATH-only gap noted above should be fixed in
the same pass — checking a PATH-and/or-env-var signal — since a scoped check
that still can't recognize env-var-signaled availability only half-closes the
underlying "false not-available" problem.

### Consequences

- Good: closes the contract gap — a future caller that naively checks
  `initialize_integrations()`'s `ok` value can't reintroduce this bug by
  passing the right capability filter.
- Good: keeps `sln doctor`'s "check everything" behavior via the `None` default.
- Bad: `validate_required_integrations()` gains a parameter and a second
  responsibility (global report vs. scoped check) — the two use cases are
  arguably different methods dressed up as one.
- Bad: doesn't fix anything by itself — every scoped call site still needs the
  follow-up change to actually pass its capability set instead of `None`.
