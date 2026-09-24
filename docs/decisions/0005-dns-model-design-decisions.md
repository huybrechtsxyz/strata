# DNS Model — v2 Design Decisions

- Status: implemented
- Date: 2026-09-21
- Revised: 2026-09-21 — removed `output_key` entirely (was ported initially,
  then pulled after finding v1 already has ≥2 more real consumers of the same
  concept, which surfaced the need for a proper shared runtime store instead
  of another ad hoc field). See [ADR-0006](0006-context-shared-stage-runtime-store.md).
- Related: [ADR-0001](0001-v1-schema-analysis-findings-for-v2.md) (v1 schema
  analysis — flags DNS's flat `value`/`var`/`secret`/`output_key` shape as the
  convention to standardize on), [ADR-0002](0002-requirement-interface-injection-grant-lessons-from-v1.md)
  (Requirement rejection, unified Value token syntax, `output_key` deferral —
  all directly implemented here for the first time), [ADR-0003](0003-provider-model-design-decisions.md)/
  [ADR-0004](0004-resource-model-design-decisions.md) (same `references`
  removal pattern applied to Provider/Resource), [ADR-0006](0006-context-shared-stage-runtime-store.md)
  (Context — why `output_key` was pulled)

## Context and Problem Statement

`DnsModel` (`src/strata/models/dns_model.py`, `src/strata/services/dns_service.py`)
is the third v2 kind, and the first one built after ADR-0002's extended
Requirement/Interface/Injection/Grant/Value/Translation analysis concluded.
Unlike Provider/Resource (ported first, then reconciled against ADR-0002
after the fact), DNS is ported with ADR-0002's conclusions already applied
from the start. This ADR records the decisions specific to DNS.

## Decisions

### 1. `spec.references`/`DnsReferencesModel` not ported

v1's `DnsSpecModel.references: DnsReferencesModel` (`variables`/`secrets` key
lists) is not ported, per ADR-0002's Requirement rejection — same removal
already applied to `Provider`/`Resource`. v1's `validate_references_declared()`
validator (checking every record's `var`/`secret` against the declared list)
is also dropped entirely: it only re-validated internal consistency within
the document, never anything external. A `${var:KEY}` token's *key* is
correctness-checked against a real `Environment` in Phase 2 once that kind
exists (deferred — see Decision 3).

### 2. `value`/`var`/`secret` unified into one `value: str` field with embedded tokens

v1's `DnsRecordModel` had four mutually-exclusive fields (`value`, `var`,
`secret`, `output_key`). Per ADR-0002's Value-token decision, `var`/`secret`
are dropped and folded into a single `value: str | None` field that may be a
literal (`"1.2.3.4"`) or contain `${var:KEY}`/`${secret:KEY}`/`${feature:KEY}`
tokens (e.g. `"${var:public_ip}"`, or composite: `"prefix-${var:region}"`).
`output_key` remains a separate field (see Decision 3). The
"exactly one of" validator is simplified from 4-way to 2-way: exactly one of
`value` / `output_key`.

A new `validate_value_tokens()` helper (`common_models.py`, shared for reuse
by `network`/`module` when built) rejects malformed `${...}` syntax at Phase 1
— unknown kind (`${vars:x}`), missing key (`${var:}`), missing colon
(`${var}`) — without needing an `Environment` to check the *key* against.

### 3. `output_key` not ported — removed for now, see ADR-0006

v1's `output_key` (a preceding deployment stage's provisioner output, e.g. a
VM's public IP feeding an A record) is real, working v1 code — not dead —
but investigating it turned up more than ADR-0002 assumed: v1 already has
≥2 other real consumers of the same "bind to a stage's output" concept
(`HealthCheckModel.output_key`, Ansible topology's `ip_output_key`), all
sharing one underlying runtime object (`ResolvedValues.stage_outputs`).
Rather than keep porting `output_key` field-by-field per kind, or
prematurely fold it into `${output:KEY}` token syntax (which would imply a
validation guarantee it doesn't have), removed entirely for now. `value` is
therefore a **required** field (no longer `| None`), and the 4-way (later
2-way) "exactly one of value/output_key" validator is gone. See
[ADR-0006](0006-context-shared-stage-runtime-store.md) for the real
generalization path (a shared "Context" store) and when `output_key`-shaped
bindings should return.

### 4. No `DnsService._validate_dynamic()` yet

v1 never had a `DnsService` at all — DNS was schema-only, Phase 1-only, even
in v1. `DnsService` is added in v2 (mirroring `ConfigurationService`'s Phase
1-only shape) so the service-layer pattern exists, but with no Phase 2 logic:
checking a `${var:KEY}`/`${secret:KEY}` token's key against a real Environment
requires the `environment` kind, which doesn't exist in v2 yet. Add
`_validate_dynamic(environment_model=...)` when it does (see ADR-0002's
worked example, already sketched there).

### 5. `PlatformKind.DNS` added; no `lifecycle` field

`DNS = "dns"` added to `PlatformKind` (fourth member, after
`CONFIGURATION`/`PROVIDER`/`RESOURCE`). Unlike `Provider`/`Resource`, v1's DNS
spec never had a `lifecycle: CommonLifecycleModel` field — not added here
either, to avoid inventing a concept v1 never needed for this kind.

## Consequences

- Good: `${var:}`/`${secret:}`/`${feature:}` token syntax and
  `validate_value_tokens()` are now real, tested code (not just ADR-0002
  prose) — ready to reuse for `network`/`module`.
- Good: no `spec.references` anywhere in v2 now (Provider, Resource, DNS all
  consistent).
- Good: composite/concatenated values (e.g. `"prefix-${var:region}"`) are
  expressible, which the old `value`/`var`/`secret` discriminated union could
  not do at all — this was the concrete motivating gap for the ADR-0002
  decision.
- Good: no `output_key` field pretending to have a validation story it
  doesn't have — removing it surfaced a bigger, more valuable finding
  (ADR-0006) instead of silently porting a v1 field that only looked simple
  because no one had checked its real consumers yet.
- Accepted cost (per ADR-0002): a token-bearing `value` field is a plain
  `str` — Pydantic can't validate the resolved value's shape (e.g. that an A
  record's literal is a valid IP) until resolution happens at build time.
  Not checked for literal values either, in v1 or here — no regression.

## Remaining Work

- `output_key`-shaped record bindings, the Environment cross-check, and the
  token resolver/router are tracked centrally, not duplicated here:
  [docs/design/value-token-resolution.md](../design/value-token-resolution.md)
  (resolver, Environment check) and
  [docs/design/provisioning-injection-model.md](../design/provisioning-injection-model.md)
  (Context/`${step:}`, for `output_key`'s eventual replacement).
