# Scope Required-Integration Checks by Command/Capability

- Status: implemented
- Date: 2026-08-24

## Context and Problem Statement

Reported by the haven team: an integration declared `required: true` in
`spec.integrations` (e.g. a `terraform`-capability entry) affected commands that
have nothing to do with that capability — e.g. `strata values get`, which only
needs secret/variable/feature store integrations, not Terraform. Haven's
workaround was to set `required: false` on the Terraform integration.

### Where this lived before implementation

`IntegrationService.validate_required_integrations()` was called unconditionally
from the end of `initialize_integrations()`
([src/strata/services/integration_service.py](../../src/strata/services/integration_service.py#L161)),
iterating **every** `spec.integrations[]` entry with `required: true` — with no
notion of which capability the invoking command actually needed. Since
`initialize_integrations()` was a process-wide singleton (only runs once per
process), whatever capability the *first* caller in the process needed would
determine what got validated, silently skipping validation for every other
caller for the rest of the process lifetime.

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

**Implemented Option B** (2026-08-24).

### Implementation Details

All 6 scoped call sites have been updated to explicitly invoke the capability-filtered check:

1. **`IdentityController._get_integration()`** → `{IIdentityProvider}` check
2. **`CostController._get_estimator()`** → `{ICostEstimator}` check
3. **`AuditController._resolve_sinks()`** → `{ISiemSink}` check
4. **`ExportAuditCommand._forward_to_siem()`** → `{ISiemSink}` check
5. **`find_available_integration_with_capability(capability)`** → forwards the caller's `capability` param
6. **`ValueController._ensure_integrations_initialized()`** → `{ISecretStore, IVariableStore, IFeatureStore}` check
7. **`DoctorSlnCommand._check_identity_integrations()`** → `{IIdentityProvider}` check

**Decoupling from `initialize_integrations()`:** `IntegrationService.initialize_integrations()` no longer calls `validate_required_integrations()` internally, fixing the singleton "first caller wins" bug — callers that need the check now call `validate_required_integrations(capabilities={...})` explicitly *after* initialization, allowing each command to check only the capabilities it cares about.

**Signature change:**
- `validate_required_integrations()` now accepts `capabilities: Optional[Set[Type]] = None`
- When `capabilities` is provided, only `required: true` specs whose declared `spec.capabilities` intersect the given set are checked
- `None` (default) checks every required integration, preserving the original unscoped behavior for potential future global-audit use cases

### Consequences

✅ Good: closes the contract gap — a future caller checking `initialize_integrations()`'s result now can't reintroduce the haven team's terraform-vs-values-get failure. Each call site declares exactly what it needs.

✅ Good: error handling is consistent — all 7 call sites log unavailable integrations at warning level (matching `ValueController`'s existing mitigation), never fatal.

✅ Good: both internal AND external scope options work — `capabilities=None` still available for hypothetical future "check everything" reports (e.g., `sln doctor`), but none of today's commands use it.

⚠️  Trade-off: each call site *must* pass its capability set; forgetting to pass it (or passing `None` naively) silently reverts to the old unscoped behavior. Addressed via module docstring examples and explicit logging.

### Deferred: PATH-only `is_available()` limitation

The related gap — `BaseIntegration.is_available()` only checks PATH, not env vars — remains out of scope per the ADR's recommendation to address it "in the same pass." Real-world instance remains: `Dockerfile.cli` (python:3.13-slim) doesn't install `git`, so even scoped checks would still fail if they tried to probe availability. Recommend capturing as a separate follow-up ADR once the availability detection pattern is better understood.
