# Terraform Deploy-Time Values Win By Rewriting the Build-Time tfvars File

- Status: accepted
- Date: 2026-10-08
- Related: [ADR-0022](0022-strata-build-run.md) (D4's render-vs-execute split
  — the reason `build run`'s file is always already present when `deploy
  run` executes), [ADR-0025](0025-strata-supplies-input-not-source-rewriting.md)
  (strata supplies input rather than rewriting sources — scoped to synced
  sources, so it permits rewriting strata's own generated build output,
  which this decision depends on), [ADR-0027](0027-strata-deploy-run.md)
  (`deploy run`'s execution model), [docs/design/terraform-variable-precedence.md](../design/terraform-variable-precedence.md)
  (full evidence record, the four-category-shape design, and how it works
  today — this ADR records only the decision itself)

## Context and Problem Statement

`deploy run` resolves every `${var:}`/`${secret:}`/`${feature:}`/`${value:}`
token strata projects into a Terraform step — across all twelve projected
categories (`dns`/`networks`/`firewalls`/`providers`/`tenant`/`topologies`/
`namespaces`/`resx_<type>`/`workspace`/`flags`/`variables`/`properties`/
`custom`) — and delivers each resolved value as a `TF_VAR_<name>`
environment variable to the Terraform CLI.

Terraform's own documented variable-definition precedence ranks environment
variables second-lowest, below any `*.auto.tfvars.json` file present in the
working directory. `build run` already wrote exactly such a file, for every
one of these categories, with every token-bearing field in its raw,
unresolved form — by design, since secrets and integration-backed values
have no value yet at build time (ADR-0022 D4: "build run renders; it does
not execute"). `build run` always runs before `deploy run` in any real
pipeline, against the identical directory (`deploy run` never re-renders).

The result: the stale, unresolved build-time file silently wins over the
correctly-resolved environment variable, in every real `terraform plan`/
`apply`. Reported as an external bug against strata 2.0.0a8; confirmed
directly against a real Terraform binary (v1.12.2) and HashiCorp's own
documented precedence rules, not assumed from reading strata's source
alone. No diagnostic is emitted at any stage — `validate`, `build run`, and
`deploy run` all report success regardless.

## Considered Options

- **Resolve these tokens at build time instead, so the file is never
  stale.** Rejected on three independent grounds, each sufficient alone:
  `build run` makes no network calls by default, so an integration-backed
  `${var:}`/`${secret:}` has no value yet to write; resolved values must
  never be written to any file, even under the opt-in `--resolve` flag
  (an existing, absolute rule); and `${output:}` tokens reference another
  execution step's Terraform outputs, which do not exist until that step
  has actually run, so they cannot be resolved before deploy time by
  construction. A partial build-time fix (only the categories/token kinds
  that happen to be build-time-safe) would reintroduce exactly the
  curated-allowlist shape an earlier, unrelated gap (universal
  resolution reach) was fixed to remove.
- **Deliver the resolved value through a higher-precedence channel than an
  environment variable** (Terraform's `-var` CLI flag, the next tier up).
  Rejected — a CLI argument is visible via `ps`/process listings,
  explicitly forbidden for secrets by this integration's own v1-evidenced
  rule, and every one of these categories can carry a secret. Moot anyway:
  `-var` is for values supplied once per invocation, not per-category
  JSON blobs matching an existing env-var convention.
- **Document the precedence interaction and do nothing further.** Rejected
  — this is a correctness and security-relevant defect (a real Terraform
  plan/apply silently uses the wrong value, including for
  resource-naming-sensitive fields), not a cosmetic documentation gap.
- **Rewrite the on-disk `*.auto.tfvars.json` file itself at deploy time
  with the resolved value** (chosen). There are exactly three channels by
  which a value can reach Terraform (CLI flag, tfvars file, environment
  variable) and none is both secret-safe and higher-precedence than the
  on-disk file — so the fix cannot come from strengthening the delivery
  channel. It has to come from making the file stop declaring a
  conflicting value in the first place.

## Decision Outcome

Chosen: **rewrite every build-time `*.auto.tfvars.json` file at deploy
time**, per step, immediately before Terraform runs (including under
`--dry-run`, which still performs a real `init`/`validate`/`plan`) —
driven by the same resolved payload the existing `TF_VAR_` delivery
already computes, never read back from the file itself. The file is
always actively rewritten, never conditionally left alone, or a stale
value would still be sitting there.

- **No secret-shaped leaf anywhere in the category:** write the fully
  resolved value directly into the file. The file legitimately wins.
- **Any secret-shaped leaf present:** blank the file to `{}` instead, so
  the (lower-precedence, but secret-safe) `TF_VAR_` environment variable
  becomes that variable's sole, uncontested source. A resolved secret is
  never written to disk, by construction — never a conditional skip that
  could be gotten wrong.
- **Flat, multi-variable categories** (`workspace`/`flags`/`variables`/
  `properties`/`custom` — each top-level key is its own independent
  Terraform variable, not one variable per category): per-key granularity
  — every key resolves and is written except the ones with a secret-shaped
  leaf, which are individually omitted rather than blanking the whole file.
- **A claimed `dns`/`network`/`firewall` document** (owned by one specific
  execution step): the rewritten file reflects only what that step is
  entitled to see — a document claimed by a *different* step is absent
  from this step's own file, matching exactly what that step's own
  `TF_VAR_` delivery already carried. File and environment variable agree
  for the first time, instead of the file silently carrying a broader,
  un-scoped set underneath.
- **`resx_<type>`:** always blanked, unconditionally, regardless of
  secrets — every resource type independently declares the same real
  Terraform variable (`resources`) in its own build-time file; rewriting
  each file independently with its own type's resolved value would
  reintroduce the identical multi-declaration collision this whole
  decision exists to fix, just one level further down. The merged
  `TF_VAR_resources` environment variable (already computed this way) is
  the sole source instead.

## Consequences

- Good: closes the defect uniformly across all twelve projected
  categories, not only the originally reported `properties`/`custom`
  scope, with no curated-category allowlist.
- Good: the "a resolved secret never reaches disk" guarantee is preserved
  exactly, by construction (omit-the-key/blank-the-file, never a
  conditional skip) — the fix strengthens this guarantee rather than
  trading it away for correctness elsewhere.
- Good: real-Terraform-binary regression tests now pin this behaviour
  directly (effective `terraform show -json` plan values), closing a gap
  where the existing stubbed test suite could not have detected this
  class of defect at all — it was asserting only on strata's own inputs
  (the delivered env var's content), never on which source Terraform
  itself actually uses.
- Bad: a step directory's `*.auto.tfvars.json` contents are no longer a
  pure function of `build run` alone — they also depend on whichever
  `deploy run` last executed against that directory. Idempotent (always
  re-derived from the in-memory resolved graph, never read back from the
  file), but a provenance/audit nuance worth a note wherever build
  artifacts are assumed immutable after `build run`.
- Neutral: the same investigation surfaced and fixed a second, independent
  defect in the same delivery mechanism (the flat-category environment
  variable delivery always JSON-encoded a value regardless of its real
  type, embedding literal quote characters into a plain scalar string that
  Terraform never strips back out) — invisible before this fix, since the
  file always won regardless, but would have surfaced for secret-shaped
  keys the moment this decision's fix landed. Fixed in the same change,
  not deferred.
- Neutral: Helm and Compose were audited directly for the same class of
  assumption and found not to share it — both already rewrite their own
  build-time output in place at deploy time, for an unrelated reason
  (ADR-0025's own carve-out for strata's own generated build output), so
  neither has a separate on-disk-file-vs-environment-variable precedence
  question to get wrong in the first place.
