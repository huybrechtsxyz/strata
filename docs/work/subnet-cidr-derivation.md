# Subnet CIDR Auto-Derivation (cidrsubnet-equivalent sizing) — Work

- Status: draft — direction decided (size-driven allocation + a persist
  command, see "Decision" below), schema/command shape not yet implemented
- Last updated: 2026-10-06
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

Given the above — and given the user's explicit confirmation that a
command which writes the computed split back into `network.yaml` is fine,
because that's exactly what makes it idempotent / git-versionable /
change-detectable — **this doc now adopts the size-driven, first-fit
allocation direction (formerly "Approach 2/3") as the chosen path**,
implemented as a separate allocate-and-persist command rather than
live derivation at `validate`/`build run` time. Approach 1
(`new_bits`+`netnum`, stable-by-construction) is kept below for the
record, but is **not** the chosen direction — it solves a narrower problem
(removes bit arithmetic only) than what was actually asked for (remove the
layout planning too), and the architect habits above (central register,
plan-before-apply, idempotent re-run, self-documenting intent) all point
at "compute once, persist, review the diff" as the right shape rather than
"recompute silently on every run."

### Sketch: command shape

```
strata network allocate NETWORK_NAME [--path PATH] [--dry-run] [--force]
```

Not designed as an isolated one-off command: thinking through this as the
network architect for a real multi-tenant hub-spoke consumer
(`cfg-int-deployment` — each customer is its own `kind: tenant`, e.g.
[config/customers/c0062.yaml](../../config/customers/c0062.yaml), each
presumably getting its own spoke VNet routed through a shared hub), a
`network` command group has a realistic multi-command roadmap, not just
this one subcommand — justifying the group the same way `version` earned
one (started as a single bare command, became a group once `new`/`update`/
`set` arrived):

- **`strata network show NETWORK_NAME`** (read-only IPAM report — address
  space, each subnet's utilization, remaining free gaps) — cheap once
  `allocate`'s gap-finding walk exists, and the single most-wanted report
  for an architect doing capacity planning.
- **`strata network check`** — cross-tenant/global overlap detection.
  Today's overlap validators (ADR-0007) only compare *explicitly peered*
  networks within one `NetworkSpecModel` — two customer spokes that are
  never declared as peered to each other but both route through the same
  hub firewall can still collide, and nothing catches that today. This is
  the "cross-manifest overlap detection" gap already flagged as deferred
  (`v1-consumer-usage.md` repo memory: "revisit once `build run` is being
  designed" — `build run` now exists) — worth reopening specifically for
  networks once this group exists.
- Longer-term, lower-priority ideas (not designed): `reserve` (headroom
  placeholder blocks), `next`/`suggest` (VNet-level allocation from an
  org-wide supernet pool — needs its own central-registry design, bigger
  scope than subnet derivation), `diagram` (reuse `strata graph`'s
  infrastructure for a VNet/subnet/peering topology view), `doctor`/`audit`
  (drift detection), `release` (deallocate — needs a cool-down/quarantine
  design, genuinely risky to build naively).

- Modeled directly on `set_version_pin()`
  (`src/strata/controllers/version_controller.py`) — a surgical
  `ruamel.yaml` round-trip edit of the network's *existing, checked-in*
  source file, not a re-dump. Only the specific subnet entries being
  allocated change; every other byte (comments, ordering, quote style,
  unrelated networks/subnets in the same file) survives untouched — this
  is what makes the resulting git diff minimal and reviewable.
- **Idempotent by construction**: only subnets with a size request
  (`host_count`/`new_bits`) and **no `cidr` yet** are candidates. A subnet
  that already has a literal `cidr` is left completely alone — re-running
  `allocate` with nothing new to allocate is a true no-op (matches
  `terraform plan`'s "no changes" and `set_version_pin()`'s re-set-same-
  value no-op).
- **Append-only**: allocation walks the *other* subnets' already-literal
  CIDRs (both hand-written ones and ones a prior `allocate` run already
  wrote) to find the first free, correctly-aligned gap for each pending
  request, in declaration order. It never moves or reconsiders an
  already-resolved subnet.
- **`--dry-run`**: prints the plan (which subnets would get which CIDR)
  without writing — the plan/apply split the architect habits above call
  for. Exit code distinguishes "would allocate N" from "nothing to do" the
  same way other dry-run-capable commands in this repo already do
  (`build run --dry-run`).
- **`--force`**: required to touch a subnet that already has a literal
  `cidr` (e.g. its declared size changed) — same refusal pattern
  `set_version_pin()` already uses for `held`/`unverified` pins. Without
  it, a changed size on an already-allocated subnet is a hard error
  ("would move already-provisioned address space — use --force"), never a
  silent recompute.

### Schema implication (not yet made)

`SubnetModel.cidr` is required today (`Field(..., min_length=1)` —
ADR-0007). Supporting "not yet allocated" subnets means `cidr` has to
become optional, paired with a new optional size field(s)
(`host_count: int | None` and/or `new_bits: int | None`), with a
`model_validator` requiring **exactly one of** `cidr` or a size field (not
both, not neither) *before* allocation — and, deliberately, **allowing
both to coexist after allocation** (per the self-documenting-intent habit
above), in which case a second validator should check the two don't
disagree (the literal's prefix length actually matches the declared
size) — catching hand-edited drift between a resolved `cidr` and its own
request, rather than letting the two silently diverge. `validate`/`build
run` should treat a subnet with a size request but still no `cidr` as a
hard error directing the author to run `strata network allocate` first —
not a second, ephemeral derivation code path — keeping exactly one place
that ever computes a literal CIDR.

## Candidate approaches considered

### Approach 1 — explicit `cidrsubnet()`-equivalent (`new_bits` + `netnum`) — NOT chosen

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

### Approach 2 — desired size, first-fit allocation over siblings already declared — CHOSEN DIRECTION

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

### Approach 3 — same as Approach 2, built on `netaddr.IPSet` instead of stdlib

Same user-facing shape as Approach 2; only the internal gap-finding
implementation differs (`IPSet` difference instead of a hand-rolled
`address_exclude()` walk). Only worth it if Approach 2's hand-rolled
version turns out to need meaningfully more logic than expected once
written — see "Is there a Python library" above for why stdlib is the
default pick.

## Open questions (remaining, after the Approach 2/3 decision above)

- **Allocation scope for Approach 2/3**: does "already claimed space" mean
  only this network's own `subnets:` list (cheap, already in scope at
  `NetworkDefinitionModel` validation time), or does it also need to see
  *other* networks' address spaces — e.g. a peered network's range, the
  same scope ADR-0007's `validate_cross_network_cidr_overlap` already
  covers? Widening the scope changes where in the validator chain this
  would need to run (currently per-`NetworkDefinitionModel`, cross-network
  overlap runs one level up at `NetworkSpecModel`).
- **Hybrid escape hatch**: should a literal `cidr:` remain valid alongside
  whichever size-based field(s) are added (mutually exclusive per subnet),
  so an author can mix "derive this one" and "I already know this one's
  address" subnets in the same network? Likely yes, but not decided.
- **Value-token interaction**: if the parent `address_space` entry carries
  a `${var:}`/`${secret:}` token, derivation can't run until that's
  resolved — same "skip if any token present" escape hatch the existing
  overlap validators already use, but needs to be re-confirmed for whichever
  approach is chosen (a derived subnet's CIDR becomes itself a deferred/
  computed value, not knowable at Phase 1 validation time, same bucket as
  Phase 2's token resolution).
- **Not yet filed upstream** — this doc is the write-up to file the actual
  feature request/design proposal from, once an approach is picked.

## Remaining Work

- [ ] Resolve the allocation-scope open question above.
- [ ] Decide the hybrid-escape-hatch (likely yes, see "Schema implication"
      above) and Value-token-interaction questions.
- [ ] Make `SubnetModel.cidr` optional + add `host_count`/`new_bits` +
      the two model_validators described in "Schema implication" above.
- [ ] Build `strata network allocate` (`version_controller.py`'s
      `set_version_pin()` as the direct template for the ruamel
      round-trip write) + its `--dry-run`/`--force` behavior.
- [ ] Wire `validate`/`build run` to hard-error on an unallocated
      size-requested subnet, directing the author to run `strata network
      allocate`.
- [ ] Prototype the allocation + validator wiring in `network_model.py`.
- [ ] File the proposal upstream (or close this out as "decided not to
      build" with the reasoning, if that's the outcome).
