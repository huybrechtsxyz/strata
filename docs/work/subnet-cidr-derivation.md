# Subnet CIDR Auto-Derivation (cidrsubnet-equivalent sizing) — Work

- Status: draft — direction decided (Approach 1b: `cidrsubnets()` does the
  carving in the real `.tf` module, strata only carries an ordered
  `(name, new_bits)` request list + a guard-rail snapshot check; see
  "Decision" below), schema/guard-rail not yet implemented
- Last updated: 2026-10-08
- Related: [ADR-0007](../decisions/0007-network-model-design-decisions.md)
  (current `NetworkModel`/`SubnetModel` shape — `cidr` is a literal-or-Value-
  token `str`, nothing is derived); spun off two sibling docs from the
  "does DNS/Firewall have the same architect itch" comparison:
  [dns-value-and-output-binding.md](dns-value-and-output-binding.md),
  [firewall-network-cidr-references.md](firewall-network-cidr-references.md)
  (Firewall is a real downstream *consumer* of this doc's CIDRs, not just a
  parallel — worth reading once this doc moves forward)

## Overview

**Subnet CIDRs are entirely hand-calculated today, not derived.** Found in
practice adding AKS's `aks_nodes`/`aks_kubeapi` subnets to a real spoke
deployment (2026-10-06): the `/25` + `/28` split is a fixed, mechanical
formula given a parent `vnet_cidr` (first `/25`-aligned block, then the next
`/28`-aligned block after it — equivalent to Terraform's own
`cidrsubnet(prefix, newbits, netnum)`), but nothing in strata or this repo
computes it. Every deployment author re-derives the literal
`address_prefixes` strings by hand, then types them into `SubnetModel.cidr`
as a plain string.

This is a **distinct ask from "Adopt `kind: network`"** (that item buys
overlap/containment *validation* of CIDRs you already typed in — see
ADR-0007 decisions #2/#3, `validate_subnet_cidr_overlap`/
`validate_subnets_fit_address_space`; it does not calculate anything for
you). This doc is about removing the manual-math step entirely: let
`SubnetModel` accept a desired *size* (host count or new-bits, "the next
`/N` block") instead of — or alongside — a literal `cidr`, and have strata
derive a concrete, collision-free `address_prefixes` from the parent
network's `address_space`, the same way `cidrsubnet()` does.

Not yet filed upstream. No schema change has been made; this doc exists so
the question and the researched options are written down before any
implementation starts.

## Current state

- `SubnetModel.cidr` (`src/strata/models/network_model.py`) is a required
  `str` — either a literal CIDR or a `${var:}`/`${secret:}`/`${feature:}`
  Value-token string (ADR-0007 decision #2). Nothing computes it.
- `NetworkDefinitionModel`'s `model_validator`s (`validate_subnet_cidr_overlap`,
  `validate_subnets_fit_address_space`) only *check* literal CIDRs the author
  already typed in — both explicitly skip validation the moment any
  participating CIDR contains a Value token (`has_value_tokens()`), since
  there is nothing to compare against until resolution. The same "skip on
  any token" escape hatch would need to carry over to any derivation feature
  for the same reason — a parent `address_space` that isn't fully literal at
  validate time can't be subnetted yet either.
- All of today's overlap/containment logic is hand-rolled directly on the
  stdlib `ipaddress` module (no third-party dependency) — `ip_network()`,
  `.overlaps()`, `.subnet_of()`. This is the existing precedent any
  derivation logic should match unless there's a concrete reason not to.

## Is there a Python library for this?

Yes — stdlib `ipaddress` already covers most of it with **zero new
dependency**:

- `cidrsubnet(prefix, newbits, netnum)`'s exact equivalent:
  `list(parent_network.subnets(new_prefix=parent_network.prefixlen + newbits))[netnum]`.
  `ip_network.subnets(new_prefix=N)` is a generator that enumerates every
  `/N` block inside the parent in address order — indexing into it by
  `netnum` reproduces Terraform's own arithmetic bit-for-bit (both are just
  "the Nth block of size `/N` within the parent", deterministic either way).
- Stdlib also has `address_exclude()` (subtract one already-carved-out
  subnet from a supernet, yielding the remaining fragments) and
  `collapse_addresses()` (merge adjacent/contiguous networks back down) —
  both of which are what a "find the next free `/N` block after whatever
  subnets already exist" allocator (the AKS `/25` then `/28` case) would be
  built on.

Third-party option, for completeness: **`netaddr`** (not currently a
dependency of this repo) adds `IPSet` set-algebra (`IPSet(parent) -
IPSet(existing_subnets)` then `.iter_cidrs()` to list free ranges directly,
without hand-rolling the gap-finding walk) and historically was the
most common Python VLSM/subnet-planning package. It would make a first-fit
allocator (see Approach 2 below) a few lines shorter, at the cost of a new
runtime dependency this repo doesn't otherwise need — stdlib `ipaddress`
can express the same algorithm without it, matching the "no new dependency"
precedent `network_model.py` already set for the overlap validators above.
**Recommendation: don't add `netaddr`** unless a concrete need for its
broader set-algebra surface (beyond one gap-finding walk) shows up later.

## Constraint: must stay stable across multiple deploys

A derived CIDR has to be the SAME literal address on every subsequent
`strata build run` for the same deployment — a Terraform-managed subnet
whose address silently shifts on a later build is a destroy/recreate, not
a no-op plan. This constraint rules out "just recompute it live at build
time" as sufficient on its own, and is the actual reason this needs
designing rather than just coding:

- `strata build run` has **no persistence mechanism today.** Its build
  output directory is wiped and fully re-rendered on every run by default
  (`build-command.md`'s own pseudocode: `should_clean` defaults to `True`
  for the default build path; `write_resolved_manifest()` writes
  `resolved.yaml` into that same wiped directory, so it's a fresh
  snapshot every run, never a prior-run reference). There is nowhere in
  the build pipeline today that reads back "what did we compute last
  time" before deriving a new value.
- The only real precedent in this repo for "compute/choose a value once,
  then keep it stable across every future run" is **version pinning**
  (ADR-0019) — specifically `set_version_pin()`
  (`src/strata/controllers/version_controller.py`), which does a surgical
  `ruamel.yaml` round-trip edit of the *existing, checked-in* `kind:
  version` document to persist one pin's value, preserving every other
  byte (comments, ordering, quote style). Once written, every later
  `strata build run`/`validate` just reads that literal value back — no
  recomputation, no drift. The same shape (compute once, surgically write
  the literal back into a committed source document, read literal
  thereafter) is the one piece of prior art to reuse here rather than
  re-invent.
- This splits the two candidate approaches below on stability grounds, not
  just schema-shape grounds (see each approach's own note):
  - **Approach 1 (`new_bits`+`netnum`) is stable by construction, no
    persistence needed at all.** Each subnet's position is pinned
    independently by its own two integers + the parent `address_space` —
    nothing about *another* subnet changes those inputs, so recomputing
    live at every `build run` always reproduces the same literal. This is
    a real, significant advantage over Approach 2/3 that the earlier pass
    of this doc hadn't weighed yet.
  - **Approach 2/3 (first-fit over siblings) is NOT stable under live
    recomputation** — adding, removing, or reordering a sibling subnet (or
    even just resizing one) can shift every subsequent derived address,
    even though nothing about the CHANGED subnet's own declaration moved.
    To honor the stability constraint, Approach 2/3 cannot derive at
    `validate`/`build run` time at all — it needs a separate, explicit
    step ("maybe another command", per the original ask) that computes
    the allocation once and **surgically writes the resulting literal
    `cidr:` back into the source YAML** (same `ruamel.yaml` round-trip
    pattern as `set_version_pin()`), after which the document holds a
    plain literal and behaves exactly like any hand-written subnet today —
    schema/validation never needs new runtime derivation logic at all,
    just a one-time authoring aid.

## How a devops/network architect actually does this (real-world IPAM practice)

Before picking an approach, worth grounding in how this is done by hand
today (the practice the original feedback was complaining about having to
do manually) — a handful of habits show up consistently in real IPAM
(IP Address Management) workflows, Azure's own subnet-sizing guidance, and
this repo's own version-pinning precedent:

- **Allocation is append-only — existing subnets never get renumbered.**
  Once a subnet is carved out and a real resource is deployed into it,
  moving it is a destroy/recreate, not a plan no-op. A new subnet request
  always gets the *next* free block; it never reshuffles anything already
  allocated. This is the single most important habit, and it's exactly
  the stability constraint from the previous section — it's not an
  incidental preference, it's the actual reason hand-calculation is
  careful/slow today.
- **A central, reviewable register of what's allocated.** Whether that's a
  spreadsheet, a NetBox/phpIPAM instance, or (in simpler shops) just the
  Terraform/YAML source itself — there is always one place that says "this
  block is taken, for this reason." For this repo, `network.yaml` already
  *is* that register (ADR-0007's whole premise); the only gap is that
  entries are typed by hand instead of appended by a tool.
- **Fixed sizing conventions per subnet *role*, not ad hoc per-deployment
  math.** A real architect doesn't re-derive "how big should the AKS node
  subnet be" each time — there's a standing convention ("AKS nodes: /25,
  AKS API server: /28, AzureFirewallSubnet: /26 minimum, AzureBastionSubnet:
  /26 minimum, GatewaySubnet: /27 recommended", etc.) applied consistently
  across every deployment. The request itself ("I need a /25 here") is the
  stable, reviewable intent; only the resulting literal address changes
  per-environment.
- **Deliberate headroom, not tight packing.** Experienced IPAM plans leave
  gaps for a subnet to grow in place later rather than packing every block
  back-to-back — but this only matters if growth-in-place is a goal; a
  pure first-fit allocator (pack tightly, append-only) is still the right
  *default* and matches what the real AKS case actually did by hand.
  Headroom, if wanted, is an explicit later refinement (e.g. an optional
  "reserve N addresses after this block"), not a blocker for a first cut.
- **Plan before apply, diff before commit.** The same `terraform plan` →
  `apply` split applies here: an architect wants to see *what would be
  allocated* before it becomes a committed, git-blessed fact — i.e. a
  `--dry-run`/preview mode is not optional polish, it's how this kind of
  change is normally reviewed (PR diff showing exactly one new subnet
  entry, nothing else touched).
- **Idempotent re-runs.** Running the planning/allocation step again with
  no new requests is a true no-op — no diff, no churn. This is the same
  property Terraform's own `plan` has, and the same property
  `set_version_pin()` already has in this repo (re-setting the same pin to
  the same value produces a no-op diff).
- **Self-documenting intent, not just a magic literal.** A year later,
  someone reading `network.yaml` should be able to tell *why* a subnet is
  `10.0.1.128/25` — "it was requested as 100 hosts" is meaningfully more
  readable than a bare address with no provenance. Keeping both the
  request (`host_count`/`new_bits`) and the resolved `cidr` side by side
  (rather than consuming/discarding the request once resolved) matches
  this habit directly.

## Decision

**Superseded (2026-10-08) — see Approach 1b below.** Everything in this
section was the original 2026-10-06 decision (an allocate-and-persist
command). Kept for the record since the evidence behind it (architect
habits, the stability constraint) is still valid — only the *conclusion*
changed, once Terraform's own `cidrsubnets()` (plural) was checked
directly against HashiCorp's docs and found to already provide the exact
stability property this section was trying to build by hand.

### The real decision: Terraform computes it, strata only carries the request

Confirmed directly against HashiCorp's own `cidrsubnets()` documentation,
not assumed: *"you must not change any of the existing arguments once
network addresses have been assigned to real infrastructure, or else
later address assignments will be invalidated. However, you can append
new arguments to existing calls safely, as long as there is sufficient
address space available."* That is exactly this doc's own stability
constraint — already solved, for free, by a Terraform builtin, as long as
subnet requests are only ever **appended**, never reordered, resized, or
removed.

This replaces the allocate-and-persist command entirely:

- **`SubnetModel.cidr` becomes optional**; a new `new_bits: int | None`
  field is added, mutually exclusive with `cidr` (one of the two
  required — same either/or validator pattern already used elsewhere in
  this codebase).
- **No new command.** The ordered list of `(name, new_bits)` pairs is
  already delivered to Terraform as-is — `networks` is already one of the
  structured broadcast categories (`_build_networks_payload()`,
  `terraform_projection.py`), so no new strata delivery mechanism is
  needed at all.
- **The real `.tf` module computes the literal CIDRs**, via `cidrsubnets()`
  (the official `hashicorp/subnets/cidr` registry module wraps this exact
  named-subnet, append-only pattern — worth pointing consumers at
  directly rather than hand-rolling the HCL):

  ```yaml
  # network.yaml
  spec:
    networks:
      - name: spoke-main
        address_space: ["10.0.0.0/22"]
        subnets:
          - name: aks_nodes
            new_bits: 3     # /22 + 3 = /25
          - name: aks_kubeapi
            new_bits: 6     # /22 + 6 = /28
  ```

  ```hcl
  locals {
    newbits = [for s in var.networks["spoke-main"].subnets : s.new_bits]
    cidrs   = cidrsubnets(var.networks["spoke-main"].address_space[0], local.newbits...)
  }

  resource "azurerm_subnet" "this" {
    for_each         = { for i, s in var.networks["spoke-main"].subnets : s.name => local.cidrs[i] }
    name             = each.key
    address_prefixes = [each.value]
    # ...
  }
  ```

  A subnet needed later is just a new entry **appended to the end** of
  `subnets:` — `aks_nodes`/`aks_kubeapi` never move.

### The real risk this still carries, and the guard-rail it needs

Append-only is not a convention an author can be trusted to remember by
hand — resizing an existing entry, inserting a new one mid-list, or
simply reordering the list (even alphabetizing it) silently reshuffles
every subnet positioned *after* the touched one. Unlike a YAML typo, this
**succeeds validation cleanly** and only surfaces as a surprise
`terraform plan` full of destroys — and because Azure generally refuses
to change a subnet's address range while real resources are deployed
inside it (a NIC/VM/private endpoint/AKS node pool holds specific IPs in
that range), the real consequence is a forced destroy/recreate of the
subnet *and* everything addressed inside it, not a quiet in-place update.

This risk is not new, or specific to choosing `cidrsubnets()` — Approach
2/3's own Python-side first-fit allocator had the identical
declaration-order sensitivity (see its own "Cons," below). Terraform
doing the arithmetic doesn't introduce the risk; it just means strata no
longer needs a whole allocator to carry it.

**Guard-rail (new, small, real work)**: strata should snapshot the
ordered `(name, new_bits)` list per network — alongside `resolved.yaml`
or a small dedicated file — and hard-error at `validate`/`build run` if
any *existing* entry's position or value changed since the snapshot,
requiring an explicit `--force` to proceed. Same refusal pattern
`set_version_pin()` already uses elsewhere in this repo for "about to
move already-provisioned address space." This is a tripwire, not an
allocator — a few dozen lines, not a new command.

### Schema implication

`SubnetModel.cidr` is required today (`Field(..., min_length=1)` —
ADR-0007). It becomes optional, paired with a new `new_bits: int | None`
field, with a `model_validator` requiring **exactly one of** `cidr` or
`new_bits` (not both, not neither). No `host_count`/`netnum` fields are
needed — `cidrsubnets()` takes additional-prefix-bits directly, and
assigns position from list order automatically, so there is nothing else
for the schema to carry. `validate`/`build run` need no new logic beyond
that validator — the literal CIDR is never computed by strata at all,
only by the consuming `.tf` module at plan/apply time.

## Candidate approaches considered


### Approach 1 — explicit `cidrsubnet()`-equivalent (`new_bits` + `netnum`) — superseded by Approach 1b

`SubnetModel` gains `new_bits: int` + `netnum: int` as an alternative to
`cidr: str` (mutually exclusive, validated like other either/or fields in
this codebase). Strata computes the literal subnet from the owning
network's `address_space[0]` (or a declared index if there's more than one)
using stdlib `subnets(new_prefix=...)[netnum]`, then runs the existing
overlap/containment validators against the *resolved* literal as today.

- Pros: smallest possible schema change; same mental model as Terraform's
  own `cidrsubnet()` (useful if an author is translating directly from an
  existing `.tf` file — see the original feedback's "terraform is possible"
  framing); fully deterministic, no allocation state to track; **stable
  across multiple deploys for free** — can be derived live every
  `validate`/`build run` with zero persistence, since each subnet's
  address depends only on its own `new_bits`/`netnum`, never on siblings
  (see "Constraint: must stay stable across multiple deploys" above).
- Cons: the author still has to pick `new_bits`/`netnum` by hand per subnet
  — it removes the *bit arithmetic*, not the *layout planning*. The real
  AKS case ("first `/25`, then the next `/28`-aligned block after it") is
  naturally `new_bits`-only for the first subnet but needs the author to
  already know where the first one ends before choosing the second one's
  `netnum` — i.e. doesn't fully remove "manual math", just shrinks it.
  **Superseded by Approach 1b**: `cidrsubnets()` (plural) auto-assigns
  position from list order, removing the need for `netnum` entirely —
  this con doesn't apply to it.

### Approach 1b — `cidrsubnets()` (plural): Terraform packs the list, append-only — CHOSEN

`SubnetModel` gains `new_bits: int` only (no `netnum`) — the ordered list
of `(name, new_bits)` pairs is delivered to Terraform unchanged (`networks`
is already a structured broadcast category), and the consuming `.tf`
module calls `cidrsubnets(address_space, newbits...)` once, which
auto-assigns each requested block's position from its place in the list.
Full design in the "Decision" section above — this entry exists so the
approach list stays a complete, comparable record.

- Pros: solves Approach 1's own con (no manual position bookkeeping, even
  for mixed sizes) without Approach 2/3's cost (no allocator to write, no
  persist command, no ruamel round-trip) — Terraform's own builtin
  already guarantees append-only stability (confirmed directly against
  HashiCorp's docs, not assumed). Zero new strata delivery mechanism
  needed; `networks` already reaches Terraform as structured data today.
- Cons: shares Approach 2/3's declaration-order sensitivity (reordering,
  resizing, or inserting mid-list reshuffles every subsequent subnet) —
  needs the guard-rail snapshot check described in "Decision" above, since
  this failure mode succeeds validation cleanly and only surfaces as a
  destructive `terraform plan`.

### Approach 2 — desired size, first-fit allocation over siblings already declared — superseded by Approach 1b

`SubnetModel` gains a `host_count: int` (or `new_bits: int`) field with no
`netnum` — strata looks at the *other* subnets already declared in the same
`NetworkDefinitionModel.subnets` list, finds the first gap in the parent
`address_space` that's large enough and correctly aligned for the requested
size, and derives the literal CIDR there. This is the one that actually
matches the feedback's "removing the manual-math step entirely" — order of
declaration in the YAML becomes the allocation order, mirroring exactly how
the real AKS case was hand-derived ("first the `/25`, then the next `/28`
after it").

- Pros: closest to what was actually asked for; no position bookkeeping for
  the author at all.
- Cons: real allocation logic to write and test (gap-finding walk over
  `address_exclude()`/manual arithmetic — not present anywhere in this repo
  today); introduces declaration-order sensitivity (reordering
  `subnets:` entries in YAML changes the computed addresses) which the
  schema/docs would need to call out explicitly; still needs the
  cross-network scope question below answered; **not stable under live
  recomputation** (see constraint section above) — requires a separate
  explicit "allocate and write back" command (ruamel round-trip, same
  pattern as `set_version_pin()`) rather than running at `validate`/`build
  run` time, which is more moving parts than Approach 1.

### Approach 3 — same as Approach 2, built on `netaddr.IPSet` instead of stdlib — superseded by Approach 1b

Same user-facing shape as Approach 2; only the internal gap-finding
implementation differs (`IPSet` difference instead of a hand-rolled
`address_exclude()` walk). Only worth it if Approach 2's hand-rolled
version turns out to need meaningfully more logic than expected once
written — see "Is there a Python library" above for why stdlib is the
default pick.

## Open questions (remaining, after the Approach 1b decision above)

- **Guard-rail snapshot shape**: where does the "last-known
  `(name, new_bits)` order" live — a new small file next to
  `resolved.yaml`, or folded into it? Keyed per network or per solution?
  Needs designing before the hard-error/`--force` check can be built.
- **Allocation scope**: `cidrsubnets()` only ever sees one network's own
  `address_space` — cross-network/cross-tenant overlap (e.g. two peered
  spokes whose parents happen to collide) is a separate, already-existing
  concern (ADR-0007's `validate_cross_network_cidr_overlap`), unaffected
  by this decision either way.
- **Hybrid escape hatch**: should a literal `cidr:` remain valid alongside
  `new_bits` (mutually exclusive per subnet), so an author can mix
  "derive this one" and "I already know this one's address" subnets in
  the same network? Likely yes, but not decided.
- **Value-token interaction**: if the parent `address_space` entry carries
  a `${var:}`/`${secret:}` token, strata's own transient validation can't
  run until that's resolved — same "skip if any token present" escape
  hatch the existing overlap validators already use. Irrelevant to the
  real CIDR computation itself, which happens in Terraform regardless.
- **Not yet filed upstream** — this doc is the write-up to file the actual
  feature request/design proposal from, once implemented.

## Remaining Work

- [ ] Resolve the guard-rail snapshot shape open question above.
- [ ] Decide the hybrid-escape-hatch and Value-token-interaction questions.
- [ ] Make `SubnetModel.cidr` optional + add `new_bits: int | None` + the
      either/or `model_validator` described in "Schema implication" above.
- [ ] Build the guard-rail snapshot check (hard-error on an existing
      entry's position/value changing since last recorded, `--force` to
      override) — a tripwire, not an allocator; no new command needed.
- [ ] Write (or link to) a reference `.tf` module snippet using
      `cidrsubnets()`/`hashicorp/subnets/cidr` for consumers to copy.
- [ ] File the proposal upstream (or close this out as "decided not to
      build" with the reasoning, if that's the outcome).

## Changelog

- 2026-10-08: Superseded the 2026-10-06 "allocate and persist" decision
  (Approach 2/3) after confirming directly against HashiCorp's own
  `cidrsubnets()` (plural) docs that Terraform already guarantees
  append-only stability natively — no custom Python allocator, no ruamel
  round-trip command needed. New chosen direction: **Approach 1b** —
  `SubnetModel` gains `new_bits` only (no `netnum`); the ordered request
  list is delivered to Terraform unchanged via the existing `networks`
  structured category; the consuming `.tf` module calls `cidrsubnets()`
  itself. Real risk surfaced and addressed: reordering/resizing/inserting
  mid-list (not just removing an entry) silently reshuffles every
  subsequent subnet's computed address, and since Azure generally refuses
  to resize a subnet with real resources already deployed in it, the
  practical consequence is a forced destroy/recreate — succeeds
  validation cleanly, only surfaces as a surprise `terraform plan`. Added
  a guard-rail requirement (snapshot the ordered request list, hard-error
  on an existing entry changing, `--force` to override — same refusal
  pattern `set_version_pin()` already uses) to catch this before it
  reaches a real plan. Rewrote "Decision"/"Schema implication"/Approach
  labels/Open Questions/Remaining Work accordingly; Approach 2/3's own
  write-ups kept, marked superseded, not deleted (still the real record
  of why `cidrsubnets()` wasn't considered the first time). No code
  changed — decision and doc update only.
