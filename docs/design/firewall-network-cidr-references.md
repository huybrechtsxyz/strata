# Firewall — Consuming Network's CIDRs via `${value:}` References — Design

- Status: current
- Last updated: 2026-10-13

## Overview

Firewall has no CIDR math of its own — ports, directions, and protocols are
enums/scalars, not carved address space. But a `FirewallRuleModel`'s
`from`/`to` is routinely a literal copy of a subnet's CIDR that's already
declared in a `network` document (e.g. an NSG rule granting the AKS node
subnet access re-types `10.0.1.128/25` by hand), with nothing keeping the
two in sync — two independently hand-typed strings that happen to agree,
until one of them changes.

`${value:kind.name.path}` ([cross-document-value-references.md](cross-document-value-references.md))
removes the duplication: `FirewallRuleModel.from_`/`.to` are Value-bound
strings ([ADR-0008](../decisions/0008-firewall-model-design-decisions.md)
decision 1), so a rule can reference a real subnet's `cidr` field directly
instead of copying it.

## Current Design

```yaml
# network.yaml
kind: network
meta:
  name: main
spec:
  networks:
    - name: main-network
      address_space: ["10.0.0.0/16"]
      subnets:
        - name: aks-nodes
          cidr: "10.0.1.0/24"
```

```yaml
# firewall.yaml
kind: firewall
meta:
  name: main-firewall
spec:
  allow:
    - direction: in
      proto: tcp
      port: 443
      from: "${value:network.main.spec.networks.main-network.subnets.aks-nodes.cidr}"
```

`_walk_value_reference_path()` (`value_references.py`) resolves each
`path` segment against the target document's own `model_dump(by_alias=True,
mode="json")`: a dict segment is a plain key lookup; a list segment is
resolved either by integer position (`subnets.0.cidr`) or, if the segment
isn't a digit, by matching a `"name"` key on one of the list's own dict
items (`subnets.aks-nodes.cidr`, as above) — the same `(kind, name)`
identity convention [ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md)
already established for document addressing, applied one level deeper to
named items *within* a document. The two can never be ambiguous:
`PlatformName`'s own pattern requires a leading lowercase letter, so a real
name is never also a valid digit-only index.

This is deliberately generic, not scoped to `Network`/`subnets` alone — the
walker never knows which `kind`/model produced the list it's walking, only
a plain `dict`/`list`, so scoping it would cost more code for an artificial
restriction, not less. In practice it activates for whatever list happens
to carry dict items with a literal `name` key (`networks`/`subnets`/
`peerings`, DNS `zones`, Workspace `provisioners`/`topology`/`resources`,
etc.) and is simply inert for lists keyed by a different field instead
(`environment.variables`/`.secrets`/`.features` use `.key`, `stage.step`
uses `.step`) — not a second field convention to support, just a miss, the
same as an unmatched integer index. Name-based addressing also sidesteps a
real correctness risk positional addressing had: a subnet's position in
`subnets:` can shift just by hand-reordering/inserting entries, silently
repointing an existing positional reference at the wrong subnet with no
error anywhere ("wrong, not missing" — the most dangerous kind of failure
mode). A failed name lookup reuses the existing `value_reference_invalid_path`
diagnostic; no new diagnostic code was needed. Duplicate names within one
list are not re-validated by the walker itself — it takes the first match
and leaves uniqueness to whichever model's own `check_unique_names()`
validator already covers that list.

**Deliberately out of scope**: a `strata firewall check` lint flagging a
literal `from`/`to` that happens to duplicate a real subnet CIDR without
using `${value:}` was considered and dropped — revisit only if a real
consumer need shows up.

## Related Decisions

- [ADR-0008](../decisions/0008-firewall-model-design-decisions.md) —
  current `FirewallRuleModel` shape (`from`/`to` as Value-bound strings).
- [cross-document-value-references.md](cross-document-value-references.md) —
  the `${value:kind.name.path}` mechanism this design consumes.
- [subnet-cidr-derivation.md](../work/subnet-cidr-derivation.md) — the
  sibling Network doc this one was originally spun out of; its own
  `strata network allocate` direction was separately cancelled, which
  turned out not to matter here (the positional-addressing risk this
  design fixes was already real without it).
- [solution-loading-and-phase2-validation.md](../work/solution-loading-and-phase2-validation.md) —
  a related but distinct concern: the Workspace subnet cross-check
  resolves `WorkspaceResourceSubnetModel.subnet` by `.name` directly on
  its own model field, not through this `${value:}` path-walking
  mechanism.

## History

- Found the `${value:}` mechanism's list-segment addressing was positional
  (integer index) only, by reading `_walk_value_reference_path()` directly
  rather than assuming — the only way to reference a specific subnet was
  by its position in `subnets:`, which silently breaks on hand-reordering
  or insertion, a "wrong, not missing" failure mode worse than a loud
  error.
- Resolved the scope question (generic vs. Network-only) in favor of
  generic: the walker has no `kind`/model context to branch on at all, so
  restricting the new behavior to Network would need *more* code, not
  less, for no real benefit — a repo-wide survey of `check_unique_names()`
  call sites confirmed plenty of other `.name`-keyed lists exist and
  benefit the same way, with zero risk to lists keyed by a different field
  (those just keep missing, as before).
- Resolved the apparent digit-vs-name ambiguity: `PlatformName`'s pattern
  (`^[a-z][a-z0-9_-]*$`) makes a name and a digit-only index mutually
  exclusive by construction, so the dispatch needed no priority rule or
  escaping convention.
- Considered and dropped a `strata firewall check` lint (flagging a
  literal CIDR duplicating a real subnet without a `${value:}` reference):
  its only buildable suggestion, before name-based addressing existed,
  would have been a positional reference — itself fragile to the exact
  reordering problem the lint was meant to guard against. Also dropped
  its original dependency on `strata network allocate`, which was
  cancelled independently (subnet-cidr-derivation.md) and turned out to
  be unrelated to the real blocker anyway.
