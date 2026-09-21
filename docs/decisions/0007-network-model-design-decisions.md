# Network Model — v2 Design Decisions

- Status: partially-implemented — model and thin service ported; multi-file
  merge behavior deliberately not ported (see Remaining Work)
- Date: 2026-09-21
- Revised: 2026-09-21 — extracted the local `_validate_cidr_string()` helper
  into `common_models.py` as `validate_cidr_or_token()`, shared with
  `firewall_model.py`'s `from`/`to` fields. No behavior change for Network.
  See [ADR-0008](0008-firewall-model-design-decisions.md).
- Related: [ADR-0001](0001-v1-schema-analysis-findings-for-v2.md) (v1 schema
  analysis — flags Network's nested `cidr: CidrSourceModel` shape as the
  outlier vs. DNS/Module's flat convention), [ADR-0002](0002-requirement-interface-injection-grant-lessons-from-v1.md)
  (Requirement rejection, unified Value token syntax — both applied here),
  [ADR-0005](0005-dns-model-design-decisions.md) (same token-unification
  pattern applied first to DNS)

## Context and Problem Statement

`NetworkModel` (`src/strata/models/network_model.py`,
`src/strata/services/network_service.py`) is the fourth v2 kind. Like DNS, it
is ported with ADR-0002's conclusions (no `references`, unified Value tokens)
already applied from the start, rather than ported-then-reconciled.

## Decisions

### 1. `spec.references`/`NetworkReferencesModel` not ported

Same removal as Provider/Resource/DNS (ADR-0002). v1's
`validate_references_declared()` (re-validating CIDR `var`/`secret` usage
against an internal declared list) is dropped entirely along with it.

### 2. `CidrSourceModel` removed — CIDR fields are unified `str` Value bindings

v1's `CidrSourceModel` (`value`/`var`/`secret` union, nested under
`subnet.cidr`) is removed entirely, per ADR-0002's Value-token decision —
the exact class ADR-0001/ADR-0002 flagged as the outlier (nested, unlike
DNS/Module's flat convention) is now gone, and the flat-vs-nested question is
moot: there's no wrapper object left to nest. `SubnetModel.cidr` and each
`NetworkDefinitionModel.address_space` entry are now plain `str` fields that
are either a literal CIDR (`"10.0.1.0/24"`) or contain `${var:KEY}`/
`${secret:KEY}`/`${feature:KEY}` tokens.

A shared `_validate_cidr_string()` helper (network_model.py) runs
`validate_value_tokens()` (common_models.py, catches malformed tokens) always,
and additionally validates literal CIDR syntax via `ipaddress.ip_network()`
**only when the string has no tokens** (checked via the new
`has_value_tokens()` helper, common_models.py) — a token-bearing value can't
be format-checked until it's resolved.

### 3. Overlap validators adapted to skip on any token presence

v1's three overlap checks (`validate_subnet_cidr_overlap`,
`validate_subnets_fit_address_space`, `validate_cross_network_cidr_overlap`)
already had a "skip if not all literal" escape hatch (couldn't compare a
`var`/`secret` reference's eventual value against anything at schema time).
Ported with the same behavior, just checking `has_value_tokens()` instead of
`.value is not None` on the old `CidrSourceModel`.

### 4. `NetworkService._validate_dynamic()` — confirmed no-op, not ported with new logic

v1's `NetworkService._validate_dynamic()` was already `return True, []`
unconditionally (docstring: *"Network has minimal cross-reference
validation — self-contained"*). v2's `NetworkService` has no Phase 2 logic
either — consistent, not a regression. Checking a `${var:KEY}`/`${secret:KEY}`
token's key against a real Environment is still deferred to when the
`environment` kind exists (same as DNS, ADR-0005).

### 5. Multi-file network merging (`merge_networks`/`merge_networkfiles`) not ported

v1's `NetworkService` also supports merging multiple `NetworkModel` documents
(networks merge by name, subnets/peerings merge by name, last-definition-wins)
— a workspace/environment composition feature, not core schema validation.
Not ported: no v2 workspace/environment layer exists yet to actually call it.
Building it now, with no consumer, would repeat the same "schema before a
real consumer" mistake already rejected for Requirement. Revisit when a v2
workspace/environment overlay concept is designed.

### 6. `PlatformKind.NETWORK` added; no `lifecycle` field

`NETWORK = "network"` added to `PlatformKind` (fifth member). Like DNS, v1's
Network spec never had a `lifecycle: CommonLifecycleModel` field — not added
here either.

## Consequences

- Good: the flat-vs-nested Value-binding inconsistency ADR-0001 flagged
  between DNS and Network is fully resolved — there's no more
  `CidrSourceModel` to be an outlier.
- Good: composite CIDR values (unlikely in practice, but structurally
  consistent with DNS/Resource) are now expressible the same way.
- Good: `has_value_tokens()` (added here, common_models.py) is a second real
  consumer of the Value-token machinery beyond `validate_value_tokens()` —
  confirms it generalizes cleanly to a second kind, as ADR-0002 hoped.
- Accepted cost (same as ADR-0005): a token-bearing `cidr`/`address_space`
  entry loses literal CIDR format checking until resolution.

## Remaining Work

- `NetworkService._validate_dynamic()` (checking token keys against a real
  `Environment`) is deferred until the `environment` kind is built.
- Multi-file network merging is not ported — revisit once a v2 workspace/
  environment overlay concept exists to actually consume it.
- `validate_value_tokens()`/`has_value_tokens()` currently only check
  *syntax*/*presence*; a full resolver is still deferred until the
  build/deploy layer is designed (ADR-0002 Decision 5).
