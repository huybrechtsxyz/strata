# Firewall Model — v2 Design Decisions

- Status: partially-implemented — model and thin service ported; multi-file
  merge behavior deliberately not ported (see Remaining Work)
- Date: 2026-09-21
- Related: [ADR-0001](0001-v1-schema-analysis-findings-for-v2.md) (v1 schema
  analysis — "Firewall Lacks Parametrization" / "No References Support",
  originally recommended adding `spec.references`), [ADR-0002](0002-requirement-interface-injection-grant-lessons-from-v1.md)
  (Requirement rejection, unified Value token syntax — resolves ADR-0001's
  gap differently than originally proposed), [ADR-0007](0007-network-model-design-decisions.md)
  (`validate_cidr_or_token()` — shared helper this ADR reuses)

## Context and Problem Statement

`FirewallModel` (`src/strata/models/firewall_model.py`,
`src/strata/services/firewall_service.py`) is the fifth v2 kind. Unlike
DNS/Network, v1's Firewall never had a `spec.references` field at all —
ADR-0001 flagged this as **the** kind-specific gap (*"Firewall is the ONLY
kind without `spec.references`"*, *"Production vs staging require duplicate
firewall files"*) and recommended fixing it by **adding** `references` to
Firewall. That recommendation is superseded: ADR-0002 rejected `references`
as a schema concept for every kind. This ADR records how the *underlying*
gap (no way to parametrize a rule's source/destination IP per environment) is
actually resolved in v2 — via the same Value-token mechanism already built
for DNS/Network, not a new field.

## Decisions

### 1. `from`/`to` become Value bindings — the real fix for ADR-0001's gap

`FirewallRuleModel.from_`/`.to` (source/destination IP or CIDR) now accept
either a literal (`"10.0.0.0/24"`) or a string containing `${var:KEY}`/
`${secret:KEY}`/`${feature:KEY}` tokens, validated by the same
`validate_cidr_or_token()` helper `network_model.py`'s CIDR fields use
(extracted to `common_models.py` in this change, ADR-0007 revised
accordingly). This directly closes ADR-0001's cited gap (parametrizing a
partner IP/CIDR per environment) using the mechanism ADR-0002 already
established, rather than a hand-authored declared-keys field.

**`port` is not made token-bindable.** Its type (`int | str | list[int | str]`)
would need to become a plain `str` to allow token embedding, at the cost of
its existing int-range validation — the same accepted trade-off ADR-0002
already flagged for typed fields, but with no cited real-world need (ADR-0001's
gap was specifically about IP addresses, not ports). Not done speculatively.

### 2. `spec.references` still not added

Confirms ADR-0002's rejection extends to Firewall too, even though ADR-0001
specifically recommended adding it here. Scoping/typo-catching is still
`Injection = Interface ∩ Environment`, deferred until that layer exists —
same as every other kind.

### 3. Found and fixed a v1 bug: `FirewallMetaModel.labels` was accidentally required

v1: `labels: Optional[Dict[str, Any]] = Field(..., description=...)` — typed
`Optional` but required via `Field(...)`, inconsistent with every other
field in the same model (`annotations`, `tags`) and every other kind's
MetaModel (`Provider`/`Resource`/`Dns`/`Network` all default `labels` to
`None`). Almost certainly a copy-paste mistake in v1 (`Field(...)` instead of
`Field(None, ...)`). Fixed here: `labels: dict[str, Any] | None = Field(None, ...)`.

### 4. `FirewallService` — thin, no Phase 2, merge deferred

Same shape as `NetworkService` (ADR-0007): no `_validate_dynamic()` logic
(checking token keys against `Environment` is deferred until that kind
exists), and v1's `merge_firewalls`/multi-file merge behavior is not
ported — it's a workspace/environment composition feature with no v2
consumer yet.

### 5. Resource↔Firewall coupling remains cut

Confirms, not a new decision: `ResourceModel`/`ResourceService` (ADR-0004)
already dropped v1's `get_merged_firewall`/`set_merged_firewall` coupling
entirely. `FirewallModel` here is fully standalone, consistent with that.

### 6. `PlatformKind.FIREWALL` added; `populate_by_name=True` for the `from`/`from_` alias

`FIREWALL = "firewall"` added to `PlatformKind` (sixth member).
`FirewallRuleModel` sets `model_config = ConfigDict(populate_by_name=True)`
(merges with `PlatformBaseModel`'s base config in Pydantic v2) so
`from_="10.0.0.0/24"` works alongside YAML's `from: 10.0.0.0/24` — `from` is
a Python keyword, same reason v1 needed the alias.

## Consequences

- Good: ADR-0001's cited real gap (environment-specific firewall IPs) is
  actually closed, using an already-built, already-tested mechanism —
  `validate_cidr_or_token()` is now proven across two kinds.
- Good: a real v1 bug (`labels` accidentally required) is fixed, not ported.
- Good: `NetworkService`/`FirewallService` now share one clear precedent for
  "no Phase 2 yet, merge deferred" — consistent shape for both.
- Accepted cost: `port` is not parametrizable — flagged, not solved,
  consistent with "don't solve a problem no one cited."

## Remaining Work

- Environment cross-check is tracked centrally in
  [docs/design/value-token-resolution.md](../design/value-token-resolution.md),
  not duplicated here.
- Multi-file firewall merging (`merge_firewalls`) is not ported — revisit
  once a v2 workspace/environment overlay concept exists (same as Network,
  ADR-0007).
- If a real need for a parametrizable `port` emerges, reconsider then —
  not speculatively now.
