# Policy Engine Architecture — v1 Capability Catalog and v2 Build Strategy

- Status: draft — catalog and sizing only, no v2 implementation started
  (umbrella doc; see "Related work docs" below for the concern-specific
  tracking docs this one coordinates). **Open Question 1 (generic engine
  vs. bespoke) is resolved (2026-10-07): bespoke, indefinitely — no
  generic `PolicyModel`/engine planned.**
- Last updated: 2026-10-07

## Overview

[gap_fit_v1.md](gap_fit_v1.md) gap #7 flags `policies` as "still fully
open" — v2 has no `PolicyModel`, no policy engine, and no equivalent of any
of v1's built-in policy types. Several concern-specific docs
([tenant-zone-policy.md](tenant-zone-policy.md), `path-conventions.md`)
already address individual policy types directly, each following the
established "model the one real check, don't port the generic engine"
precedent. This doc is the umbrella catalog those feed into — it exists to
answer one question precisely, now that it's come up directly: **if v2
built out "a good strata data export + Terraform plan output," would that
cover all of v1's policy types?** Answer, verified against v1's real
source (`e:\SourcesXYZ\strata\src\strata\validators\policies\*.py`,
19 files — read directly, not from memory): **no.** Roughly a third need
nothing but strata's own resolved document data, a couple genuinely need
Terraform plan JSON, and just under half need a completely separate data
source (SBOM, a CVE scanner, Checkov, Infracost, or an LLM) that neither
strata's document graph nor a Terraform plan can produce.

Correcting this doc's own source material as it goes: the doc that first
catalogued v1's policy types said "18 types" and "four `sbom_*` types" —
both undercounts by one. There are **19** real policy files on disk, and
**five** `sbom_*` types (`sbom_allowed_registries`, `sbom_denied_packages`,
`sbom_license`, `sbom_max_components`, `sbom_pinned_versions`) — a fifth,
`sbom_license`, was missed in the earlier count.

## v1's real `PolicyContext` (`base_policy.py`) — the actual data surface

```python
@dataclass
class PolicyContext:
    phase: str
    work_path: Optional[Path]
    deployment_service: Optional[Any] = None
    configuration_service: Optional[Any] = None
    solution_controller: Optional[Any] = None
    platform_artifact: Optional[Any] = None       # strata's own rendered build model
    plan_data: Optional[Dict[str, Any]] = None    # terraform show -json output
    build_path: Optional[Path] = None
    sbom_components: Optional[List[Any]] = None   # List[SbomComponentModel]
    cve_audit_result: Optional[Any] = None        # populated when --audit ran
    cost_data: Optional[Dict[str, Any]] = None    # cost.json (Infracost) contents
    file_path: Optional[Path] = None              # file being validated (path_convention)
    change_reference: Optional[Any] = None        # ChangeReferenceModel (ADR-0074)
```

Every one of the 19 policy types reads from exactly one (sometimes two) of
these fields. Classifying by which field(s) each reads is what the table
below does — it is the actual, verified answer to "what data does this
policy need," not a guess from the catalog doc's prose summary.

## Classification — all 19 real v1 policy types, by data source

| Policy type                  | `PolicyContext` field(s) read                              | Data source category                                                                       |
| ---------------------------- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `required_labels`            | `platform_artifact`                                        | **A — pure strata document data**                                                          |
| `naming_pattern`             | `deployment_service`/`configuration_service` (names)       | **A — pure strata document data**                                                          |
| `ref_convention`             | `configuration_service` (`spec.remotes[].conventions`)     | **A — pure strata document data**                                                          |
| `path_convention`            | `file_path` + `configuration_service` (`spec.paths`)       | **A — pure strata document data**                                                          |
| `layer_agreement`            | `file_path`/`work_path` + path conventions                 | **A — pure strata document data**                                                          |
| `change_reference_required`  | `change_reference` (CLI flag / env var only)               | **A — no data dependency at all**                                                          |
| `tenant_zone`                | `plan_data` + `configuration_service` (zones)              | **B — needs real Terraform plan JSON**                                                     |
| `resource_type_restrictions` | `plan_data` (real TF resource-type strings)                | **B — needs real Terraform plan JSON**                                                     |
| `cost_threshold`             | `cost_data` (written by **Infracost**)                     | **C — needs a cost-estimator integration**                                                 |
| `ai_review`                  | `plan_data` fed to an `ai_agent` integration (an LLM call) | **C — needs an AI integration (and isn't rule-based at all)**                              |
| `cve_max_severity`           | `cve_audit_result` (trivy/grype over the SBOM)             | **C — needs a CVE scanner integration**                                                    |
| `checkov`                    | `build_path` (real Checkov binary scanning rendered IaC)   | **C — needs the Checkov integration**                                                      |
| `sbom_allowed_registries`    | `sbom_components`                                          | **C — needs SBOM generation**                                                              |
| `sbom_denied_packages`       | `sbom_components`                                          | **C — needs SBOM generation**                                                              |
| `sbom_license`               | `sbom_components`                                          | **C — needs SBOM generation**                                                              |
| `sbom_max_components`        | `sbom_components`                                          | **C — needs SBOM generation**                                                              |
| `sbom_pinned_versions`       | `sbom_components`                                          | **C — needs SBOM generation**                                                              |
| `script`                     | whatever JSON is handed to it                              | **generic mechanism, not a data source**                                                   |
| `opa`                        | whatever input document strata assembles                   | **generic mechanism, not a data source** (already a real v1 type — this wasn't a new idea) |

**Tally: 6 of 19** need nothing beyond strata's own resolved document
values (bucket A). **2 of 19** need a real Terraform plan (bucket B) — this
is exactly the "full plan-time tier" [tenant-zone-policy.md](tenant-zone-policy.md)
already sizes for `tenant_zone` specifically. **9 of 19** (bucket C) need an
entirely separate external integration regardless of how complete strata's
own graph/plan data ever gets. The remaining 2 (`script`, `opa`) are
evaluation mechanisms, not data sources — `opa` in particular is worth
noting explicitly: using OPA/Rego as a generic policy evaluator isn't a new
proposal, it's literally one of v1's own 19 built-in types, already
precedented.

## Why "assemble all the data and hand it to OPA" doesn't dissolve bucket C

Raised directly in conversation and worth recording precisely, since it's
an easy conclusion to over-generalize to: once every input in the table
above actually exists (SBOM, CVE results, cost data, Checkov findings, an
AI risk rating), a single generic OPA/Rego evaluator **could** express the
pass/fail logic for all 19 types — that's architecturally true, and v1's
own `opa` type already demonstrates it (never required a bespoke Python
class; always an option). But that observation doesn't shrink the real
work:

- **OPA replaces the evaluation step, not the data-production step.** Every
  bucket-C integration (SBOM collectors, a CVE scanner, Infracost, Checkov,
  an AI agent) still has to be built and still has to run — OPA doesn't
  produce any of that data, it only evaluates rules against data someone
  else already produced.
- **Checkov specifically can't be "replaced" by Rego** — it is itself a
  full policy engine with thousands of built-in checks. Re-expressing those
  in Rego would mean reimplementing Checkov, not gating on data it already
  supplies. The correct shape keeps Checkov as the scanner; only the
  severity-gate decision on top of its findings is rule-evaluable (and
  that part was always trivial — never where the real work was).
- **`ai_review` can't be "replaced" by Rego at all** — the actual judgment
  comes from an LLM call (probabilistic), not a deterministic rule. OPA
  can gate on the risk level the LLM *returns*, but can't replace making
  the call.

The genuine architectural win from a generic `opa`/`script` dispatcher is
narrower than "covers everything for free": it replaces N bespoke Python
`BasePolicy` subclasses with one dispatcher + a library of versioned
`.rego` files, so adding a new check becomes a policy-bundle change instead
of a strata code change and release. That's real, but it's an evaluation-
layer simplification, not a data-source one.

## v2 current state

Nothing exists yet: no `PolicyModel`, no `validators/policies/` package, no
`integrations/checkov.py` or `integrations/cve_scanner.py` (confirmed via
`list_dir` on `src/strata/{models,integrations}` — v2 has `sbom_model.py`
and `sbom_collectors/` already, per
[sbom-generation.md](sbom-generation.md)'s Phase 1, but nothing downstream
of the SBOM, and nothing for any bucket-A/B policy type either).

## Related work docs (concern-specific, each tracked independently)

- [tenant-zone-policy.md](tenant-zone-policy.md) — `tenant_zone` (bucket B).
  Static config-time tier implemented; full plan-time tier sized, not built.
- `docs/design/path-conventions.md` — `path_convention` (bucket A). Resolved.
- [sbom-generation.md](sbom-generation.md) — the SBOM half of bucket C
  (`sbom_components` production). Phase 1 implemented; CVE scanning and the
  `sbom_*` policy types explicitly marked out of scope there, picked up by
  this doc's siblings below.
- `docs/archive/checkov-integration.md` — `checkov` (bucket C). Shipped
  end to end, archived.
- `docs/archive/cve-scanner-integration.md` — `cve_max_severity` (bucket
  C). Shipped end to end, archived.
- [gap_fit_v1.md](gap_fit_v1.md) gap #7 — the original "still fully open"
  flag this whole family of docs is resolving piece by piece.

Not yet separately tracked (bucket A/C items with no dedicated doc):
`required_labels`, `naming_pattern`, `ref_convention`, `layer_agreement`,
`change_reference_required` (all bucket A — cheap, no external dependency,
likely a single combined doc or direct implementation when picked up),
`resource_type_restrictions` (bucket B, shares `plan_data` plumbing with
`tenant_zone`'s full tier — worth building alongside it, not separately),
`cost_threshold` (needs an Infracost integration), `ai_review` (needs an
`ai_agent` integration — a materially bigger, separate concern given it's
not rule-based at all), `script`/`opa` (generic dispatchers — only useful
once at least one real policy type exists to compare against).

## Open Questions

1. **Does v2 want a generic `PolicyModel`/`PolicyEngine` at all, or does
   the "model the one real check directly" precedent extend to every
   policy type v2 ever builds? Decided (2026-10-07): bespoke,
   indefinitely — no generic engine planned.** Checked against real usage
   first, not assumed: `cfg-int-deployment`'s real `config/policies.yaml`
   (the one real consumer repo with any policy infrastructure at all —
   `haven` has none, confirmed by direct `Get-ChildItem` search, zero
   `policies.yaml` anywhere) has exactly two **live, uncommented**
   policies (`tenant_zone`'s `zone-isolation`, `path_convention`'s
   `enforce-path-conventions`) — every other declared entry, **including
   both of v1's own generic escape hatches, `script` and `opa`**, sits
   commented out as an inert example, exactly like every unused bespoke
   bucket-C type next to it. A direct search of that repo's real Azure
   Pipelines YAML (`.azure/**`) for `type: opa`/`type: script`/`opa eval`/
   `OPA_ENDPOINT` returned zero matches — not configured, and never
   invoked. This is decisive: v1 already ships a working `opa`/`script`
   dispatcher at zero marginal build cost to this consumer, and it is
   **exactly as unused** as the bespoke types it would supposedly make
   unnecessary. There is no real evidence anywhere that genericness
   itself is a felt need — only that the *specific, still-unconfigured*
   bucket-C types are unused, which says nothing about the dispatch
   mechanism underneath them.

   Cross-checked against this doc's own "Why OPA doesn't dissolve bucket
   C" finding above: a generic engine's real win is narrower than it
   looks — it only simplifies the *evaluation* step, already "the
   simplest part of this entire feature" for every bucket-C type shipped
   so far (confirmed directly in [cve-scanner-integration.md](../archive/cve-scanner-integration.md)'s
   own Sizing section, written before this question was answered). It
   does nothing for the real work — SBOM collectors, the CVE scanner,
   Checkov's own rule engine, Infracost, an AI agent — each of which has
   to be built and has to run regardless of which evaluator checks its
   output.

   v2's own real track record now has 3 shipped instances of the bespoke
   pattern (`tenant_zone`'s static tier, `path_convention`, `cve_policy`),
   each a small, independent model field + inline evaluator function, no
   shared dispatcher, no reported friction. Checkov will be a 4th — not
   evidence the pattern is straining, since each instance costs the same
   small, fixed amount regardless of how many came before it; a generic
   engine would still have to accommodate Checkov's own genuinely
   different shape (real per-provisioner degradation, a configuration
   surface an order of magnitude bigger than any other bucket-C type —
   see [checkov-integration.md](checkov-integration.md)) just as easily
   as the current bespoke approach already does.

   **Decision: no generic `PolicyModel`/engine is planned.** Continue the
   established "model the one real check directly" precedent for Checkov
   and every future policy type. Building a dispatcher now, before any
   real adopter has asked for the one thing it would uniquely provide
   (next paragraph), would be exactly the "declared-but-unread machinery"
   anti-pattern [audit-trail.md](audit-trail.md)'s own "Lessons from v1's
   own defects" #1 warns against, just applied to the policy layer
   instead of audit.

   **The one concrete trigger that would reopen this, named explicitly
   rather than left open-ended:** none of the bucket-A/B/C types (bespoke
   or not) let an *operator* express a check strata itself doesn't
   already know about — that is the one real, narrow value `script`/`opa`
   uniquely provide (an extensibility escape hatch), orthogonal to
   "codify more of v1's 19 known types faster." If a real consumer ever
   asks for a custom, strata-unaware rule, the right answer is a small,
   dedicated `script` policy type (run a command, JSON on stdin, exit
   code signals pass/fail — bucket-A-cheap, no Rego/OPA-server
   dependency), not a full `PolicyModel` with a per-type configuration
   sub-schema. Revisit only then, scoped to exactly that need.
2. Build order across the bucket-C integrations — SBOM-dependent types
   (5 `sbom_*` + `cve_max_severity`, 6 of 9) share one data source already
   half-built; Checkov and Infracost are both net-new integrations with no
   existing v2 scaffolding. Likely cheapest-first order: finish the
   SBOM-dependent types before starting Checkov/Infracost/AI from scratch.
3. **No real-usage evidence gathered yet for any bucket-C policy type —
   partially answered above.** `cve_max_severity` itself was already
   checked directly in [cve-scanner-integration.md](../archive/cve-scanner-integration.md)'s
   own Open Question 1 (commented-out, unconfigured in both real repos).
   Checkov, Infracost, and `ai_review` remain unchecked — worth
   confirming before investing in any of them, same discipline just
   applied to this question's own `opa`/`script` check above.

## Changelog

- 2026-10-06: Created — umbrella catalog answering a direct question
  ("would a good strata graph/output + terraform/provisioner output cover
  all of v1's policies?") with a verified classification of all 19 real
  policy types (not 18/"four sbom_*" as an earlier doc summarized — both
  undercounts, corrected here) by which `PolicyContext` field each reads.
  Recorded the follow-up finding that assembling all required data and
  handing it to a generic OPA evaluator is architecturally sound but does
  not shrink the real work (data production, not evaluation, is bucket C's
  actual cost), with two concrete non-collapsible exceptions (`checkov`'s
  existing rule library, `ai_review`'s non-deterministic LLM judgment).
  Cross-linked from [tenant-zone-policy.md](tenant-zone-policy.md).
- 2026-10-07: Resolved Open Question 1, per direct request to look at the
  next open item in [cve-scanner-integration.md](../archive/cve-scanner-integration.md)'s
  own `## Remaining Work` checklist (which pointed back here). Checked
  real usage directly rather than reasoning
  from precedent alone: `cfg-int-deployment`'s real `config/policies.yaml`
  has both of v1's own generic dispatchers, `script` and `opa`, commented
  out as inert examples — identically to every unused bespoke bucket-C
  type — and a direct search of that repo's real Azure Pipelines YAML
  found zero invocations of either. `haven` has no policy infrastructure
  at all. **Decided: no generic `PolicyModel`/engine, ever, absent a named
  trigger** — continue "model the one real check directly" for Checkov
  and beyond; the one concrete reopening trigger (an operator needing a
  strata-unaware custom rule) is named explicitly, with its own cheap
  fallback (a dedicated `script` type), rather than left open-ended.
  Updated [checkov-integration.md](../archive/checkov-integration.md)'s Sizing #4/
  Open Question 3 and [cve-scanner-integration.md](../archive/cve-scanner-integration.md)'s
  `## Remaining Work` item to point here rather than duplicate the
  reasoning. No code changed — decision and doc update only.
