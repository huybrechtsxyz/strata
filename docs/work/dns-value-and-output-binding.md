# DNS — Output/Value Binding & Report/Check Commands — Work

- Status: draft — question captured, no decision, no implementation
- Last updated: 2026-10-06
- Related: [subnet-cidr-derivation.md](subnet-cidr-derivation.md) (spun out
  of that doc's cross-kind "does DNS/Firewall have the same architect itch
  as Network" comparison, 2026-10-06), [ADR-0005](../decisions/0005-dns-model-design-decisions.md)
  (current `DnsRecordModel` shape), [ADR-0006](../decisions/0006-context-shared-stage-runtime-store.md)
  (Context — status `proposed`, not built), [cross-document-value-references.md](../design/cross-document-value-references.md)
  (`${value:kind.name.path}` — status `implemented`)

## Overview

Unlike Network, DNS has **no CIDR-style math to derive** — a hostname/record
value isn't arithmetically carved from a parent the way an address block is,
so there's no DNS equivalent of `cidrsubnet()` and no analog to `strata
network allocate`. But the same underlying architect frustration ("stop
hand-typing a value that's actually determined by something else") shows up
in DNS in a different shape, worth tracking separately since it depends on
different, mostly-not-yet-built machinery.

## The two real asks

### 1. Bind a record's value to a provisioner output (`output_key`, not ported)

v1 had `DnsRecordModel.output_key` — a record's value sourced from a
preceding deployment stage's provisioner output (e.g. point an `A` record at
whatever public IP the Application Gateway ends up with, instead of
hand-typing it once Terraform decides it). **Confirmed not ported**, and
the current model's own docstring (`src/strata/models/dns_model.py`) is
explicit about why — `value` today only documents
`${var:KEY}`/`${secret:KEY}`/`${feature:KEY}`, deliberately **not**
`${output:KEY}`:

> folding it into the token syntax as `${output:KEY}` prematurely would
> misrepresent it as having the same validation guarantees as
> `var`/`secret`/`feature` (which will be checkable against a real
> Environment; `output` has no equivalent ground truth, in v1 or here).
> Re-add once Context is built.

[ADR-0006](../decisions/0006-context-shared-stage-runtime-store.md)
(Context) is still `Status: proposed` — not built. So this ask is
genuinely blocked, not just undocumented.

**Open question, not yet verified against real code**: `value-token-resolution.md`
states `${output:step.key}` already exists as a token *kind* somewhere in
this repo's Value-token machinery (referenced from
[cross-document-value-references.md](../design/cross-document-value-references.md)
as existing prior art). Whether that existing `${output:}` support is
general-purpose (usable from any field, DNS included) or scoped to
wherever it was originally built for is **not confirmed** — needs a real
code check (`value_tokens.py`, `semantic_checks.py`) before assuming DNS
could adopt it as-is. If it turns out to be already generic, the real
blocker shrinks to "does the output actually exist to bind to" (Context),
not "does the token syntax support it."

### 2. `${value:kind.name.path}` may already work in DNS today — docstring looks stale

Separately from (1): [cross-document-value-references.md](../design/cross-document-value-references.md)
added a 5th Value-token kind, `${value:kind.name.path}`, as a **generic,
solution-wide walk** over every string field in every loaded document
(`unresolved_value_tokens()`/`find_malformed_value_tokens()`) — not
opt-in per kind. `DnsRecordModel.value`'s own syntax validator
(`validate_value_token_syntax()`) calls the same shared
`validate_value_tokens()` helper every other Value-bound field uses, with
no DNS-specific restriction visible in the field itself. This suggests a
DNS record could likely already reference another document's field today
(e.g. `value: "${value:tenant.c0062.meta.name}"`-style), **but
`DnsRecordModel`'s own docstring was written before `${value:}` existed
and still only lists `var`/`secret`/`feature`** — i.e. the docstring may
simply be stale, the same class of drift already seen elsewhere in this
repo (docstrings not updated when a cross-cutting feature landed after
they were written). **Not yet confirmed either way** — needs a quick real
test (`DnsRecordModel.model_validate(...)` with a `${value:...}` token,
plus checking whether `strata validate`'s Phase 2 walk actually reaches
DNS documents) before updating the model docstring or filing this as a
real gap.

## Report/check command ideas (same shape as Network's, no new mechanism needed)

- **`strata dns show ZONE`** — zone/record inventory report: record counts
  per type, TTL outliers, approaching a provider's record-count limits.
  Read-only, cheap, same "architect wants a report" shape as
  `strata network show` (see subnet-cidr-derivation.md's command-group
  roadmap section).
- **`strata dns check`** — cross-tenant hostname collision detection: two
  different customer zone files both declaring the same record
  (`name`+`type`+zone) is a real conflict in a multi-tenant platform like
  `cfg-int-deployment`, and nothing catches it today (today's DNS
  validation is file-local, same gap Network had before this round of
  design work). Direct analog of the cross-tenant CIDR-overlap `check`
  command idea.

## Why this probably isn't its own command group

Two candidate commands (`show`/`check`), neither needing new allocation-style
state, doesn't clear the bar a `network` group cleared (3+ commands with a
real roadmap). More likely these fold into `strata validate`
(cross-tenant `check`) and a general-purpose reporting surface, if/when
built, rather than justifying a dedicated `dns` command group on their own.

## Remaining Work

- [ ] Verify whether `${output:step.key}` is already generic/usable from
      any Value-bound string field, or scoped to a specific consumer —
      read `value_tokens.py`/`semantic_checks.py` directly.
- [ ] Verify whether `${value:kind.name.path}` already resolves correctly
      from a real `DnsRecordModel.value` today (write a throwaway test),
      and update the model's stale docstring either way.
- [ ] If Context (ADR-0006) ever gets built, re-add `output_key`/
      `${output:KEY}` support to `DnsRecordModel` per its own docstring's
      explicit "re-add once Context is built" note.
- [ ] Decide whether `show`/`check` are worth building at all without a
      concrete, cited real-consumer need (matches this repo's own
      "evidence over assumption" convention) — no real trigger found yet,
      unlike Network's (a real AKS deployment hitting the manual-math
      problem).
