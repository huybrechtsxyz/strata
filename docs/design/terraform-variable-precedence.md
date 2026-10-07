# Terraform Deploy-Time Value Delivery — Design

- Status: current
- Last updated: 2026-10-08

## Overview

Every value strata resolves for a Terraform step at deploy time — across all
twelve projected categories (`dns`/`networks`/`firewalls`/`providers`/
`tenant`/`topologies`/`namespaces`/`resx_<type>`/`workspace`/`flags`/
`variables`/`properties`/`custom`) — must reach the real Terraform CLI
correctly, including values sourced from secrets and other deploy-time-only
stores that `build run` cannot resolve. This doc describes how that delivery
works today: a combination of rewriting `build run`'s own
`*.auto.tfvars.json` files with resolved values and, for secret-shaped
content, an environment variable (`TF_VAR_<name>`) instead — chosen per
category so a resolved secret never reaches disk.

The mechanism exists to close a real, externally-reported defect: Terraform's
own variable-definition precedence ranks environment variables below any
`*.auto.tfvars.json` file, so environment-variable-only delivery (this
mechanism's original design) was silently shadowed by the stale,
unresolved file `build run` always writes first. See
[ADR-0031](../decisions/0031-terraform-deploy-time-tfvars-file-rewrite.md)
for the decision this doc implements, and this doc's own "History" section
below for how that was found and why the alternatives were rejected.

## Current Design

### The two delivery channels, chosen per category

`deploy_controller.py`'s per-step loop, immediately before invoking
Terraform (`init`/`validate`/`plan`/`deploy`, including under `--dry-run`,
which still runs a real preview — never under `--smoke-test`, which makes
no tool contact at all and never reaches this code):

1. **Rewrite every build-time `*.auto.tfvars.json` file** this step's
   directory already has, via
   [`resolve_deploy_time_files()`](../../src/strata/integrations/terraform_projection.py)
   (`terraform_projection.py`, next to `planned_files()`, the build-time
   counterpart it mirrors). Every category present is always actively
   rewritten — never conditionally skipped, or a stale value would still be
   sitting there from `build run`.
2. **Deliver every category's resolved value as `TF_VAR_<name>`** too
   (`deploy_controller.py`'s existing per-step loop, unchanged in shape by
   this mechanism) — named via `real_variable_name(category)`
   (`terraform_projection.py`) so a real Terraform root's own
   `TF_VAR_<declared_variable_name>` actually matches what the rewritten
   file declares under the same key.

Which of the two actually determines the effective value Terraform uses
depends on whether the category contains a secret-shaped leaf (any string
containing a `${secret:...}` token, applied per-string — a single
interpolated string mixing `${var:}`/`${secret:}` is secret-shaped as a
whole):

- **No secret anywhere:** the file carries the fully resolved value. The
  file legitimately wins (Terraform's own precedence), and the `TF_VAR_`
  env var — though also set, redundantly — agrees with it.
- **Any secret present:** the file is blanked to `{}` instead, so the file
  declares nothing for that variable and the `TF_VAR_` env var becomes its
  only, uncontested source. A resolved secret is never written to disk.

### Four category shapes

`resolve_deploy_time_files()` covers three of these directly (the fourth,
`resx_<type>`, is handled by a separate, simpler, always-unconditional step
right next to it in `deploy_controller.py` — see below):

1. **Flat, multi-variable categories** (`FLAT_CATEGORIES` —
   `workspace`/`flags`/`variables`/`properties`/`custom`) — the file is a
   bare `{key: value}` map; each key is its own independent Terraform
   variable. Resolved with `resolve_value_tokens_tracking_secrets()`
   (`value_tokens.py`) — every key is written except one with a
   secret-shaped leaf beneath it, which is individually omitted (not the
   whole file blanked) and left for that key's own `TF_VAR_<key>` to
   deliver instead.
2. **Single-variable broadcast categories** (`providers`/`tenant`/
   `topologies`/`namespaces`) — the file wraps one dict under one real
   Terraform variable name (`real_variable_name(category)`). Any secret
   anywhere taints the whole variable — there is no per-key granularity to
   fall back on, unlike shape 1 — so the whole file is blanked.
3. **Claimed/broadcast document categories** (`dns`/`networks`/
   `firewalls`) — identical rule to shape 2, except the payload resolved is
   this step's own claimed/broadcast view (`docs_for_step`, computed once
   per step and shared with the `TF_VAR_` delivery below it, not
   duplicated) rather than the workspace-wide payload. A document claimed
   by a *different* execution step is therefore absent from this step's
   own rewritten file — matching exactly what that step's own `TF_VAR_`
   value already carried, so file and environment variable agree for the
   first time rather than the file silently carrying a broader, un-scoped
   set underneath.
4. **`resx_<type>`** — always blanked to `{}`, unconditionally, by a small
   dedicated step, never routed through `resolve_deploy_time_files()` at
   all. Every active resource type writes its own
   `resx_<type>.auto.tfvars.json` file at build time, but every one of
   them independently declares the *same* real Terraform variable,
   `resources` — rewriting each file independently (even only when
   secret-free) would just move today's multi-file, same-variable
   collision one level down instead of fixing it. The already-existing
   merged `TF_VAR_resources` environment variable (computed once per step
   from every active type's resolved payload) is the sole source instead,
   every time.

### The flat-category environment variable's own encoding rule

A `FLAT_CATEGORIES` key's `TF_VAR_<key>` value is `json.dumps()`-encoded
only when the resolved value isn't already a plain string — a plain string
is passed through to the environment variable raw. This matches
HashiCorp's own documented `TF_VAR_name=value` convention (no JSON quoting
needed or expected for a string-shaped variable) and `tf_var_env()`'s own,
separate, pre-existing convention for `ValueResolution.values` — JSON-
encoding a plain string here would embed literal quote characters into the
variable's content that Terraform does not strip back out.

## Related Decisions

- [ADR-0031](../decisions/0031-terraform-deploy-time-tfvars-file-rewrite.md)
  — the decision this doc implements: rewrite the build-time file rather
  than rely on environment-variable delivery alone, and why.
- [ADR-0022](../decisions/0022-strata-build-run.md) — D4's render-vs-execute
  split: `deploy run` always executes against the exact directory `build
  run` already rendered, never re-rendering it, which is why a stale file
  is always present to rewrite.
- [ADR-0025](../decisions/0025-strata-supplies-input-not-source-rewriting.md)
  — strata supplies input rather than rewriting sources, scoped to synced
  sources; a `*.auto.tfvars.json` file is strata's own generated build
  output, not a synced source, so this mechanism's rewrite does not
  conflict with that rule.
- [ADR-0027](../decisions/0027-strata-deploy-run.md) — `deploy run`'s
  execution model (the step loop this mechanism's rewrite/delivery sits
  inside).
- [terraform-tfvars-parity.md](terraform-tfvars-parity.md) — defines the
  `TF_VAR_<name>` naming/wrapper conventions (`real_variable_name()`,
  `FLAT_CATEGORIES`) this mechanism reuses throughout.
- [build-time-value-categories.md](build-time-value-categories.md) — the
  build-time no-network/no-secrets rules (`build run` resolves `constant`/
  `environment` stores only, and never writes a resolved value to any
  file even under `--resolve`) that rule out resolving these tokens at
  build time instead.

## History

- An external bug report (strata 2.0.0a8) traced a `${value:}` token in a
  deployment's `properties` field surviving, unresolved, into a real
  `terraform plan` — confirmed directly against a real Terraform binary
  and HashiCorp's own documented variable-definition precedence, not
  assumed from strata's source alone: `*.auto.tfvars.json` ranks above
  environment variables, the reverse of this mechanism's original design
  assumption. The scope turned out to be every one of the twelve projected
  categories this delivery mechanism covers, not only the two originally
  reported.
- Build-time resolution was considered and rejected on three independent
  grounds: `build run` makes no network calls by default (an
  integration-backed value has nothing to resolve yet); a resolved value
  must never be written to any file, even under the opt-in `--resolve`
  flag; and `${output:}` tokens reference another execution step's
  Terraform outputs, which cannot exist before that step has actually run.
- A higher-precedence delivery channel (Terraform's `-var` CLI flag) was
  considered and rejected — visible via process listings, explicitly
  forbidden for secrets by this integration's own v1-evidenced rule, and
  every covered category can carry one.
- `resx_<type>` needed a different rule from every other category, found
  while designing the fix: each resource type's file independently
  declares the *same* real Terraform variable (`resources`), so resolving
  each file's own value independently would have reintroduced the exact
  multi-declaration collision this mechanism exists to prevent, one level
  further down. Always blanking every `resx_<type>` file and relying
  solely on the already-existing merged `TF_VAR_resources` env var
  sidesteps the question rather than needing to answer it.
- A claimed `dns`/`network`/`firewall` document's rewritten file was
  deliberately scoped to only what its owning execution step can see,
  matching that step's own already-step-scoped environment variable
  delivery exactly — a document claimed by a different step is absent
  from this step's own file, not merged in stale/unresolved.
- Fixing this surfaced a second, independent, previously invisible defect
  in the very same delivery mechanism: the flat-category environment
  variable delivery always JSON-encoded a value regardless of its real
  type, which embeds spurious literal quote characters into a plain
  scalar string that Terraform never strips back out. It had been fully
  invisible until this fix landed, since the stale file always won
  regardless of what the environment variable actually contained — fixed
  in the same investigation rather than deferred, since it would have
  surfaced for secret-shaped keys the moment the precedence fix shipped.
- Neither defect could be observed by a stubbed `run_command` test, since
  both are facts about real Terraform CLI behaviour, not about strata's
  own code — the existing (stubbed) test suite had asserted only on the
  content strata handed to Terraform, never on which source Terraform
  itself actually used, and stayed green throughout. A dedicated test
  harness invoking a real `terraform` binary (skipped when absent from
  `PATH`) was added specifically because no stub could have caught this
  class of defect.
- Helm and Compose were audited directly for the same class of assumption
  and found not to share it: both already rewrite their own build-time
  output in place at deploy time, for an unrelated, pre-existing reason
  (ADR-0025's carve-out permits rewriting strata's own generated build
  output), so neither has a separate on-disk-file-vs-environment-variable
  precedence question to get wrong in the first place.
- A code review after the fix shipped found a third, real defect the
  implementation itself (and its 100+ passing tests) had missed:
  `build_dns_networks_firewalls_payloads()` unconditionally returns all
  three of `dns`/`networks`/`firewalls`, even as empty dicts — unlike
  `build_configuration_payloads()`, which already filters out empty
  categories. Merging the always-three-keys dict directly into the
  rewrite step created three brand-new `*.auto.tfvars.json` files, every
  deploy run, for every workspace — including ones declaring none of
  these categories at all — violating this mechanism's own "never create
  a file `build run` did not produce" rule. No existing test caught it
  because none asserted on the *complete* set of files in a step
  directory after a deploy, only on specific files' contents. Fixed by
  filtering on the workspace-wide payload's own emptiness before merging,
  not the per-step claimed/broadcast view (those two can disagree: a
  document claimed entirely by a different step still means a real file
  exists to rewrite). A dedicated regression test now asserts the
  complete file listing is unchanged by a deploy run that uses none of
  these categories.
