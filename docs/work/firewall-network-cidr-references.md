# Firewall — Consuming Network's CIDRs via `${value:}` References — Work

- Status: draft — question captured, no decision, no implementation
- Last updated: 2026-10-06
- Related: [subnet-cidr-derivation.md](subnet-cidr-derivation.md) (spun out
  of that doc's cross-kind "does DNS/Firewall have the same architect itch
  as Network" comparison, 2026-10-06 — this is where Firewall becomes a
  real downstream consumer of that work, not just a parallel), [ADR-0008](../decisions/0008-firewall-model-design-decisions.md)
  (current `FirewallRuleModel` shape), [cross-document-value-references.md](../design/cross-document-value-references.md)
  (`${value:kind.name.path}` — status `implemented`, the mechanism this doc
  proposes stressing further)

## Overview

Unlike Network, Firewall has **no math of its own to derive** — ports,
directions, and protocols are enums/scalars, not carved address space. But
Firewall is the most direct **consumer** of Network's CIDR values: a rule's
`from`/`to` is routinely a literal copy of a subnet's CIDR that's already
declared in `network.yaml` (e.g. an NSG rule granting the AKS node subnet
access re-types `10.0.1.128/25` by hand). Nothing today keeps the two in
sync — they're two independently hand-typed strings that happen to agree.

This was a background duplication annoyance before
[subnet-cidr-derivation.md](subnet-cidr-derivation.md)'s `strata network
allocate` design; it becomes a real correctness risk once that lands —
`allocate` can legitimately reallocate/append subnet CIDRs, and a firewall
rule holding a stale hand-copied literal would then silently grant access
to the wrong range (or no longer match the subnet it was meant to) with no
error anywhere.

## The fix already exists — `${value:kind.name.path}`

[cross-document-value-references.md](../design/cross-document-value-references.md)
(status: **implemented**) already lets any Value-bound string field
reference another document's scalar field, with Phase 2 existence/path
validation and deploy-time substitution. `FirewallRuleModel.from_`/`.to`
are already Value-bound strings (`validate_cidr_or_token()`, ADR-0008
decision 1) — syntactically, a rule should already be able to write:

```yaml
from: "${value:network.main-network.spec.networks.0.subnets.0.cidr}"
```

instead of a literal, today, with no new token kind needed. **Not yet
verified against real code** — needs an actual test, same as the DNS doc's
equivalent open item, before claiming this works end to end.

## The sharp edge: positional, not name-based, path addressing

Read `value_references.py`'s `_walk_value_reference_path()` directly: list
segments are resolved **only by integer index**
(`if not segment.isdigit() or int(segment) >= len(node): return
_VALUE_PATH_MISSING`) — there is no way to address a list element by one of
its own fields (e.g. its `name`). So the only way to reference a specific
subnet today is by its position in `NetworkDefinitionModel.subnets`
(`networks.0.subnets.0.cidr`), never by name
(`networks.vnet-main.subnets.aks_nodes.cidr`).

This is a latent footgun that gets much sharper once `strata network
allocate` is real: appending a new subnet to the `subnets:` list is exactly
the allocator's normal, expected behavior, but inserting one **before** an
existing, already-referenced entry (or simply reordering the list by hand)
silently shifts every subsequent positional index — a `${value:}` reference
pointing at "index 2" doesn't error, it just **silently starts resolving to
a different subnet**, the same "wrong, not missing" failure mode that's
far more dangerous than a loud validation error.

## Candidate fix: name-based path segments for lists

Extend `_walk_value_reference_path()` so a non-integer segment, when the
current node is a list of mappings, can match one of the list's own
`name`-bearing items first. Sketch: for a list node, if the segment is not
a digit, scan the list for a dict item whose own `"name"` key equals the
segment (matching the exact `(kind, name)` identity convention ADR-0015
already established for document addressing — applied one level deeper,
to named items *within* a document). Falls back to the existing
digit-only integer indexing when the segment is numeric, so no existing
`${value:...}` reference changes behavior.

- Pros: directly removes the positional footgun above; consistent with
  how this repo already addresses *documents* by name rather than
  position (ADR-0015) — this just extends the same principle one level
  into list fields that are themselves name-keyed (networks, subnets,
  peerings all have `name` — same shape `check_unique_names()` already
  enforces uniqueness for).
- Cons: `_walk_value_reference_path()`'s existing behavior is relied on by
  the "implemented" cross-document-value-references.md design as already
  shipped and tested — this is a **new, backward-compatible extension**
  to an already-finished feature, not a bug fix, and per this repo's own
  `docs/design/` convention, shouldn't be edited into that already-"done"
  doc in place; it belongs in its own follow-on work (this doc), graduating
  separately if built.
- Scope question: does this need to support *every* named list in the
  schema generically, or only `NetworkDefinitionModel.subnets`/`.networks`
  (the concrete case found)? Generic is more consistent with how
  `check_unique_names()`/`References()` are already applied uniformly
  across kinds; scoped-to-Network is less work and matches "don't solve a
  problem no one cited" if no other kind's named lists are actually
  referenced this way yet.

## Report/lint command ideas

- **`strata firewall check`** (or folded into `strata network check`/
  `strata validate`) — flag a firewall rule's literal `from`/`to` that
  happens to exactly equal a real subnet's CIDR declared elsewhere in the
  solution but is **not** expressed as a `${value:}` reference — a "you
  should probably reference this instead of duplicating it" lint, surfaced
  before it has a chance to silently drift.
- **Drift detection**: a literal `from`/`to` that *used to* match a real
  subnet's CIDR (by name, via some prior recorded association) but no
  longer does, after a reallocation — needs the literal lint above as a
  prerequisite; not designed further here.

## Why this probably isn't its own command group

Same conclusion as the DNS doc: one or two lint-style commands with no
allocation-style state of their own most likely fold into `strata network
check`/`strata validate` rather than justifying a dedicated `firewall`
command group.

## Remaining Work

- [ ] Verify `${value:network.NAME.spec.networks.N.subnets.M.cidr}`
      actually resolves end to end from a real `FirewallRuleModel.from_`/
      `.to` today (write a throwaway test) before treating it as already
      usable.
- [ ] Read `_walk_value_reference_path()`'s full implementation and its
      existing test coverage before committing to the name-based-segment
      design above — confirm there's no existing partial support and no
      test that would need to change.
- [ ] Decide the scope question (generic named-list addressing vs.
      Network-only) above.
- [ ] If built: needs its own `## History`-worthy test coverage for both
      the fallback-to-integer-index behavior (no regression) and the new
      name-matching behavior.
- [ ] Prototype the `strata firewall check` / `strata network check`
      literal-duplication lint once `strata network allocate` itself
      exists (no point linting against a feature that doesn't exist yet).
