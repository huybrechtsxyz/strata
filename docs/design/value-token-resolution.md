# Value Token Resolution — Design

- Status: partially-implemented
- Last updated: 2026-09-24

## Overview

The unified `${var:KEY}`/`${secret:KEY}`/`${feature:KEY}` embedded-token
mechanism, adopted in [ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md)
to replace v1's per-kind `value`/`var`/`secret` discriminated unions
(`ValueSourceModel`, superseded). This doc tracks the mechanism's current
state across kinds so the same "Environment cross-check deferred" note
doesn't need repeating in every kind's ADR.

## Current Design

- **Syntax**: `${kind:KEY}` where `kind` is `var`, `secret`, or `feature`
  (future: `step`, once [Context](provisioning-injection-model.md) lands).
  Embeddable anywhere inside a plain `str` field — a literal is just a
  string with no tokens in it.
- `validate_value_tokens()` (`common_models.py`) — Phase 1 syntax check only
  (rejects unknown kind, missing key, missing colon). Cannot check whether
  `KEY` actually exists anywhere — that's Phase 2.
- `has_value_tokens()` — lets a kind skip literal-format checks (e.g. CIDR
  syntax) when a token is present, since the value isn't known until
  resolution.
- The actual **resolver/router** (partial regex substitution across a whole
  leaf; a leaf containing any `${secret:...}` token is secret-shaped and
  routed to a deploy-time-only channel) is designed (ADR-0002 Decision 5)
  but **not implemented** — this is separate from the Phase 1 syntax
  validators above.

## Per-Kind Status

| Kind | Value-token field(s) | Phase 1 syntax check | Phase 2 Environment cross-check |
| --- | --- | --- | --- |
| Provider | none identified | n/a | n/a |
| Resource | none (`configuration` is raw passthrough, not token-aware) | n/a | n/a |
| DNS | `DnsRecordModel.value` | Done ([ADR-0005](../decisions/0005-dns-model-design-decisions.md)) | Deferred — needs `environment` kind |
| Network | `SubnetModel.cidr`, `NetworkDefinitionModel.address_space` entries | Done ([ADR-0007](../decisions/0007-network-model-design-decisions.md)) | Deferred — needs `environment` kind |
| Firewall | `FirewallRuleModel.from_`/`.to` | Done ([ADR-0008](../decisions/0008-firewall-model-design-decisions.md)) | Deferred — needs `environment` kind |
| Module | `ModuleServiceEnvironmentModel.value` | Done ([ADR-0009](../decisions/0009-module-model-design-decisions.md)) | Deferred — needs `environment` kind |
| Namespace, Topology, Workspace | none yet | n/a | n/a |

## Related Decisions

- [ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md) — token syntax decision, resolver design, rationale for rejecting `ValueSourceModel`/Jinja
- [ADR-0005](../decisions/0005-dns-model-design-decisions.md), [ADR-0007](../decisions/0007-network-model-design-decisions.md), [ADR-0008](../decisions/0008-firewall-model-design-decisions.md), [ADR-0009](../decisions/0009-module-model-design-decisions.md) — per-kind adoption
- [ADR-0006](../decisions/0006-context-shared-stage-runtime-store.md) — Context, the `${step:}` token this doc doesn't cover yet

## Remaining Work / Open Questions

- Build the `environment` kind, then wire `_validate_dynamic(environment_model=...)`
  on `DnsService`/`NetworkService`/`FirewallService`/`ModuleService` — one
  shared pattern, four call sites (sketched already in ADR-0002's worked
  example).
- Build the actual resolver/router (partial regex substitution + secret-
  shaped-leaf routing) — deferred until the build/deploy layer is designed.
  Not started.
- Add `${step:step_name.output_key}` as a fourth token kind once Context
  ([ADR-0006](../decisions/0006-context-shared-stage-runtime-store.md)) is
  built, and re-add an output-sourced binding to `DnsRecordModel`.

## Changelog

- 2026-09-24: Created, consolidating the recurring "Environment cross-check
  deferred until `environment` kind exists" remaining-work item duplicated
  across ADR-0005/0007/0008/0009.
