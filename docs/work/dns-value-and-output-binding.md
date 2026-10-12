# DNS — Output/Value Binding & Report/Check Commands — Work

- Status: draft — output/value binding question (part 1) RESOLVED and
  FIXED 2026-10-12 (`DnsRecordModel`'s docstring corrected to match real
  behavior); report/check commands (part 2) remain an open, undecided idea
- Last updated: 2026-10-12
- Related: [subnet-cidr-derivation.md](subnet-cidr-derivation.md) (spun out
  of that doc's cross-kind "does DNS/Firewall have the same architect itch
  as Network" comparison, 2026-10-06), [ADR-0005](../decisions/0005-dns-model-design-decisions.md)
  (current `DnsRecordModel` shape), [cross-document-value-references.md](../design/cross-document-value-references.md)
  (`${value:kind.name.path}` — status `implemented`), [ADR-0036](../decisions/0036-cross-run-key-value-output-store.md)
  / [cross-run-step-output-delivery.md](../archive/cross-run-step-output-delivery.md)
  (the real mechanism that ended up satisfying what this doc called
  "Context")

## Overview

Unlike Network, DNS has **no CIDR-style math to derive** — a hostname/record
value isn't arithmetically carved from a parent the way an address block is,
so there's no DNS equivalent of `cidrsubnet()` and no analog to `strata
network allocate`. But the same underlying architect frustration ("stop
hand-typing a value that's actually determined by something else") shows up
in DNS in a different shape, worth tracking separately since it depends on
different, mostly-not-yet-built machinery.

## The two real asks

### 1. Bind a record's value to a provisioner output (`output_key`, not ported) — RESOLVED (2026-10-12): DNS can already do this, the model's own docstring is stale

v1 had `DnsRecordModel.output_key` — a record's value sourced from a
preceding deployment stage's provisioner output (e.g. point an `A` record at
whatever public IP the Application Gateway ends up with, instead of
hand-typing it once Terraform decides it). The model's own docstring
(`src/strata/models/dns_model.py`) still says `value` only documents
`${var:KEY}`/`${secret:KEY}`/`${feature:KEY}`, deliberately **not**
`${output:KEY}` — "folding it into the token syntax... prematurely would
misrepresent it as having the same validation guarantees... Re-add once
Context is built." **That premise no longer holds, checked directly
against real code and tests, not assumed:**

- `${output:step.key}` is real, built, and **DNS is explicitly one of the
  three document kinds it supports** — dns/networks/firewalls, via the
  step-ownership mechanism (`docs/work/gap_fit_v1.md` gap #12,
  `WorkspaceService.claimed_document_names()`). A real, passing,
  end-to-end test confirms it:
  `test_deploy_run_resolves_output_token_in_dns_via_owning_step_targets`
  (`test_deploy_controller.py`). `DnsRecordModel.value` can reference
  `${output:step.key}` today, as long as the DNS zone is claimed by an
  owning execution step's `targets` — no schema change needed, it
  already works.
- "Context" (ADR-0006, cited by this doc and the model's docstring as
  the real blocker — "re-add once Context is built") was never built as
  ADR-0006 itself described it (a dedicated Pydantic model) — but the
  underlying need it existed to satisfy **was** built, differently: a
  plain in-memory `dict` (`deploy_controller.py`'s `step_outputs`) for
  the same-invocation case, plus `DeploymentOutputStore`
  ([ADR-0036](../decisions/0036-cross-run-key-value-output-store.md))
  for the cross-run case this doc never originally anticipated either.
  See [provisioning-injection-model.md](../archive/provisioning-injection-model.md)'s
  own 2026-10-12 re-check for the full story of that divergence.

**Net effect: `DnsRecordModel`'s own docstring is now factually wrong**,
not just stale phrasing — it says `${output:KEY}` is deliberately
unsupported, but a real, tested code path already supports it. **Fixed
(2026-10-12)** — the docstring now documents `${output:STEP.KEY}` (and
`${value:kind.name.path}`, part 2 below) as real, supported syntax, with
the claiming requirement spelled out; the stale "Environment doesn't
exist in v2 yet" parenthetical and the dead "re-add once Context is
built" framing are both removed.

### 2. `${value:kind.name.path}` may already work in DNS today — RESOLVED (2026-10-12): confirmed, docstring fixed

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
they were written). **Confirmed (2026-10-12), not just suggested:**
`semantic_checks.py`'s `_check_value_references()` calls
`resolve_document_value_references()` against `index.all()` — every
loaded document, unconditionally, no per-kind opt-in — so DNS documents
are reached exactly like any other kind. Docstring fixed alongside part
1's fix above.

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

- [x] ~~Verify whether `${output:step.key}` is already generic/usable from
      any Value-bound string field, or scoped to a specific consumer~~ —
      **RESOLVED (2026-10-12), see part 1 above**: scoped to dns/network/
      firewall (and modules reached via a targeted namespace) via the
      step-ownership mechanism, confirmed by a real passing test.
- [x] ~~Verify whether `${value:kind.name.path}` already resolves correctly
      from a real `DnsRecordModel.value` today~~ — **RESOLVED (2026-10-12),
      see part 2 above**: confirmed solution-wide via `index.all()`, no
      DNS-specific restriction; docstring fixed.
- [x] ~~If Context (ADR-0006) ever gets built, re-add `output_key`/
      `${output:KEY}` support to `DnsRecordModel`~~ — **MOOT (2026-10-12)**:
      Context (ADR-0006's own dedicated Pydantic model) was never built
      and is now an archived, dead direction
      ([provisioning-injection-model.md](../archive/provisioning-injection-model.md)).
      Nothing to re-add — `${output:KEY}` token support already works for
      DNS today, per part 1 above; only the model's own docstring (not
      its behavior) needs the small factual fix part 1 already describes.
- [ ] Decide whether `show`/`check` are worth building at all without a
      concrete, cited real-consumer need (matches this repo's own
      "evidence over assumption" convention) — no real trigger found yet,
      unlike Network's (a real AKS deployment hitting the manual-math
      problem).
