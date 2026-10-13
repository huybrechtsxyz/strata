# Firewall — Consuming Network's CIDRs via `${value:}` References — Work

- Status: in-progress — the `strata firewall check` lint was considered and
  dropped 2026-10-13; name-based path-segment addressing was designed and
  implemented 2026-10-13 (`_walk_value_reference_path()`); the original
  "verify `${value:}` resolves end to end from Firewall" item is now
  closed by that same change's test coverage
- Last updated: 2026-10-13
- Related: [subnet-cidr-derivation.md](subnet-cidr-derivation.md) (spun out
  of that doc's cross-kind "does DNS/Firewall have the same architect itch
  as Network" comparison, 2026-10-06 — this is where Firewall becomes a
  real downstream consumer of that work, not just a parallel), [ADR-0008](../decisions/0008-firewall-model-design-decisions.md)
  (current `FirewallRuleModel` shape), [cross-document-value-references.md](../design/cross-document-value-references.md)
  (`${value:kind.name.path}` — status `implemented`, the mechanism this doc
  proposes stressing further), [solution-loading-and-phase2-validation.md](solution-loading-and-phase2-validation.md)
  (a related but distinct concern, built 2026-10-06 — the Workspace subnet
  cross-check resolves `WorkspaceResourceSubnetModel.subnet` by `.name`,
  not by list position, so it doesn't share this doc's positional-addressing
  footgun below, though both touch the same `Network`/subnet surface)

## Overview

Unlike Network, Firewall has **no math of its own to derive** — ports,
directions, and protocols are enums/scalars, not carved address space. But
Firewall is the most direct **consumer** of Network's CIDR values: a rule's
`from`/`to` is routinely a literal copy of a subnet's CIDR that's already
declared in `network.yaml` (e.g. an NSG rule granting the AKS node subnet
access re-types `10.0.1.128/25` by hand). Nothing today keeps the two in
sync — they're two independently hand-typed strings that happen to agree.

This was a background duplication annoyance even before considering any
future Network tooling: a subnet's position in `subnets:` can already
shift today just by hand-reordering/inserting entries in `network.yaml`,
silently invalidating any positional `${value:...}` reference a firewall
rule held — the same "wrong, not missing" risk this doc originally framed
around `strata network allocate` (since cancelled — see
[subnet-cidr-derivation.md](subnet-cidr-derivation.md)'s Decision
section), which only would have made the existing risk more frequent, not
unique to it.

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

instead of a literal, today, with no new token kind needed. **Verified
end to end (2026-10-13)**:
`test_firewall_rule_references_a_named_subnet_cidr_end_to_end`
(`test_value_controller.py`) proves a real `FirewallRuleModel.from_`
resolving a `${value:...}` reference to a Network subnet's `cidr`.

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

**Design finalized 2026-10-13** (both open items below resolved against
real code, not assumed):

```python
def _walk_value_reference_path(node: Any, segments: list[str]) -> Any:
    for segment in segments:
        if isinstance(node, dict):
            if segment not in node:
                return _VALUE_PATH_MISSING
            node = node[segment]
        elif isinstance(node, list):
            if segment.isdigit():
                if int(segment) >= len(node):
                    return _VALUE_PATH_MISSING
                node = node[int(segment)]
            else:
                match = next(
                    (item for item in node if isinstance(item, dict) and item.get("name") == segment), None
                )
                if match is None:
                    return _VALUE_PATH_MISSING
                node = match
        else:
            return _VALUE_PATH_MISSING
    return node
```

- **No new diagnostics code needed.** A failed name lookup returns the
  same `_VALUE_PATH_MISSING` sentinel the digit-index branch already
  returns — `resolve_document_value_references()` reports it as the
  existing `value_reference_invalid_path`, unchanged.
- **No ambiguity between a name and a digit index, by construction**:
  `PlatformName` (`common_models.py`) is `pattern=r"^[a-z][a-z0-9_-]*$"` —
  every real document/list-item name *must* start with a lowercase
  letter, so a name can never also be `.isdigit()`. The existing
  `if segment.isdigit()` check is therefore never a real choice between
  two valid interpretations; it's only ever "is this plainly numeric, or
  not."
- **Duplicate names within one list are not re-validated here.** The walk
  takes the *first* matching item (list order) and leaves uniqueness
  enforcement entirely to whichever model's own `check_unique_names()`
  validator already covers that list (confirmed: `NetworkDefinitionModel.
  validate_unique_subnet_names()`/`validate_unique_peering_names()`,
  `NetworkSpecModel.validate_unique_network_names()` all already do, for
  the concrete Network case) — not every named list in the schema has
  such a validator (e.g. `ResourceModel.volumes` only checks uniqueness
  among volumes that *do* have a name set), so the walker deliberately
  doesn't assume uniqueness as its own precondition.

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
  separately if built. One line in that doc ("integer segments index into
  lists", `cross-document-value-references.md` line ~162) would need a
  small factual correction once this ships, since it would no longer be
  exclusively true — a narrow, cited exception to the "don't edit a
  finished doc" rule, the same class of fix the DNS docstring got
  2026-10-12.
- **Scope question — RESOLVED (2026-10-13): generic, not Network-only.**
  `_walk_value_reference_path()` has zero visibility into which `kind` or
  model produced the list it's currently walking — it only ever sees a
  plain `dict`/`list` from `model_dump()`. Scoping the new branch to "only
  when inside a Network document" isn't mechanically simpler, since
  there's no `kind` parameter to branch on without threading one through
  purely to narrow this check — it would be *more* code for an artificial
  restriction, not less. The generic rule ("list of dicts, try matching
  `name` first") naturally only ever activates for whichever lists happen
  to carry dict items with a literal `name` key — confirmed via a
  repo-wide `check_unique_names()` survey: `networks`/`subnets`/
  `peerings` (Network), `sinks` (Audit), `paths` (Configuration), `zones`
  (DNS), `services` (Module), `modules` (Namespace), `remotes` (Solution),
  `provisioners`/`topology`/`resources`/`volumes` (Workspace) all use
  `.name`; a handful of other lists key off a *different* field instead
  (`environment.variables`/`.secrets`/`.features` use `.key`, `stage.step`
  uses `.step`, `topology.components` uses `.role`) — those are simply
  unaffected (no `"name"` key present, so the match always misses and
  falls through to `_VALUE_PATH_MISSING`, same as today), not a second
  field convention this change needs to add support for.

## Report/lint command ideas

- **`strata firewall check`** (or folded into `strata network check`/
  `strata validate`) — flag a firewall rule's literal `from`/`to` that
  happens to exactly equal a real subnet's CIDR declared elsewhere in the
  solution but is **not** expressed as a `${value:}` reference — a "you
  should probably reference this instead of duplicating it" lint, surfaced
  before it has a chance to silently drift. **Considered and dropped
  2026-10-13**: the only `${value:...}` reference this lint could suggest
  today is positional (`networks.0.subnets.0.cidr`), which is itself fragile
  to reordering — the exact problem this doc's "sharp edge" section already
  flagged. A lint that recommends a fragile fix isn't worth shipping; the
  real prerequisite is the name-based path addressing proposed below, not
  `strata network allocate` (which was itself cancelled — see
  [subnet-cidr-derivation.md](subnet-cidr-derivation.md)'s Decision section,
  "Approach 1b" superseding the original allocate-and-persist design).
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

- [x] ~~Verify `${value:network.NAME.spec.networks.N.subnets.M.cidr}`
      actually resolves end to end from a real `FirewallRuleModel.from_`/
      `.to` today (write a throwaway test) before treating it as already
      usable.~~ — **DONE (2026-10-13)**:
      `test_firewall_rule_references_a_named_subnet_cidr_end_to_end`
      (`test_value_controller.py`) proves a real `FirewallRuleModel.from_`
      resolving `${value:...subnets.aks-nodes.cidr}` end to end.
- [x] ~~Read `_walk_value_reference_path()`'s full implementation and its
      existing test coverage before committing to the name-based-segment
      design above — confirm there's no existing partial support and no
      test that would need to change~~ — **DONE (2026-10-13)**: read in
      full; no partial support exists today (list segments are
      `.isdigit()`-only); existing coverage is `test_value_controller.py`'s
      `resolve_document_value_references()` suite (integer-index/whole-
      object/not-scalar cases) plus `test_semantic_checks.py`'s wiring
      tests — none assume list segments can *only* ever be digits, so none
      need to change.
- [x] ~~Decide the scope question (generic named-list addressing vs.
      Network-only) above~~ — **DONE (2026-10-13): generic** — see
      "Candidate fix" section above.
- [x] ~~Implement the algorithm above in `_walk_value_reference_path()`,
      update its docstring, and fix the one now-stale line in
      `cross-document-value-references.md` ("integer segments index into
      lists").~~ — **DONE (2026-10-13)**: implemented in
      `value_references.py`; docstring updated; the one stale line in
      `cross-document-value-references.md` corrected.
- [x] ~~Needs its own test coverage for: fallback-to-integer-index (no
      regression — every existing `test_value_controller.py` case must
      still pass unmodified), new name-matching resolving a real
      `FirewallRuleModel.from_`/`.to` end to end (closes the item above),
      a name that doesn't match any item (still `value_reference_invalid_path`),
      and a list of plain scalars (e.g. `address_space`/`targets`) with a
      non-digit segment (still misses, not a crash).~~ — **DONE
      (2026-10-13)**: five new tests added to `test_value_controller.py`
      (named subnet, positional index — previously untested at all, unknown
      name, non-digit segment against a scalar list, and the firewall
      end-to-end case); full suite (2512 tests) + mypy + ruff + lint-imports
      all clean.

## Changelog

- 2026-10-13: Dropped the `strata firewall check` literal-CIDR-duplication
  lint (companion to DNS's own `_check_dns_records()`, which was built
  instead) — its only buildable `${value:}` suggestion today is positional
  and fragile to reordering, and its old dependency on `strata network
  allocate` no longer applies since that command was cancelled.
- 2026-10-13: Finalized the name-based path-segment design (both open
  questions resolved against real code): generic (not Network-scoped,
  since the walker has no `kind` context to scope by anyway), and the
  digit-vs-name branch is unambiguous by construction thanks to
  `PlatformName`'s pattern (names can never be all-digit). Not yet
  implemented — see Remaining Work.
- 2026-10-13: Implemented. `_walk_value_reference_path()` now matches a
  non-digit list segment against a `"name"` key on the list's own dict
  items, falling back to integer indexing unchanged. No new diagnostics
  code needed (reuses the existing `_VALUE_PATH_MISSING` ->
  `value_reference_invalid_path` path). This also means a Firewall rule's
  `from`/`to` can now reference a Network subnet by name
  (`${value:network.NAME.spec.networks.NET.subnets.SUBNET.cidr}`) instead
  of position — the original motivation for this whole doc.
