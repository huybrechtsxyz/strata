# Terraform variable precedence — deploy-time delivery is silently overridden — Work

- Status: draft
- Last updated: 2026-10-07

## Overview

Every deploy-time value strata resolves for a Terraform step is delivered as a
`TF_VAR_<name>` environment variable. Terraform's documented variable-definition
precedence places environment variables **second-lowest**, below every
`*.auto.tfvars.json` file — and `build run` has already written exactly such a
file, containing the same variable names with their tokens **unresolved**, into
the exact directory `deploy run` then executes in.

The result: the stale, unresolved build-time file wins, and strata's correctly
resolved value is silently discarded. Terraform plans and applies with the
literal token string (`"${value:tenant.c0224.meta.name}"`). No diagnostic is
emitted at any stage — `validate`, `build run`, and `deploy run` all report
success.

This doc tracks the investigation, the fix design, and the phased build-out. It
is not in `docs/design/` because nothing is built yet. It is expected to produce
an ADR (the precedence decision and the secrets-on-disk boundary it forces) and
then graduate to `docs/design/`.

## The bug, as verified

Reported against strata 2.0.0a8 / Terraform 1.12.2 / azurerm 4.81.0, for a
`${value:}` token in an environment document's `spec.properties.*`. Every link
in the chain was re-verified directly against this repo's source. The report is
accurate, and the scope is wider than it claims.

### 1. `build run` writes the token to disk, unresolved

`_build_properties_payload()` and `_build_custom_payload()`
([terraform_projection.py](../../src/strata/integrations/terraform_projection.py#L474))
return `graph.properties` / `graph.custom` verbatim. No token resolution is
applied. `planned_files()` writes them as `properties.auto.tfvars.json` /
`custom.auto.tfvars.json`.

This part is by design and is not itself the bug — `build run` deliberately
resolves nothing that needs a secret or a network call (see "Why build-time
resolution cannot be the fix" below).

### 2. `deploy run` resolves correctly, then delivers via the losing channel

[deploy_controller.py](../../src/strata/controllers/deploy_controller.py#L686)'s
`FLAT_CATEGORIES` loop calls `resolve_value_tokens_in_mapping(payload, tokens)`
and sets `env[f"{integration.ENV_VAR_PREFIX}{key}"]`, i.e. `TF_VAR_<key>=<json>`.
The resolution is correct. The delivery channel is not.

The surrounding comment states the assumption that makes this wrong:

> an env-var-sourced value overrides the same-named `-var-file` entry

It does not. The same assumption is written into
[value-token-resolution.md](../design/value-token-resolution.md) and
[terraform-tfvars-parity.md](../design/terraform-tfvars-parity.md), so this is a
design-level error, not a coding slip.

### 3. Both commands target the same directory, by design

`deploy_run()`'s own docstring describes `build_path` as "Where `build run`
already rendered artifacts — never re-rendered here" (ADR-0022 D4's
render-vs-execute split), and both controllers derive the per-step working
directory identically (`build_path / step.name`). The stale file is therefore
guaranteed to be present whenever `deploy run` executes, in any real pipeline.

### 4. Terraform's real precedence

Confirmed against the official documentation
(<https://developer.hashicorp.com/terraform/language/values/variables>),
highest to lowest:

| # | Source                                                    | Used by strata        |
| - | --------------------------------------------------------- | --------------------- |
| 1 | `-var` / `-var-file` on the CLI, and HCP Terraform        | yes (non-secrets only) |
| 2 | `*.auto.tfvars` / `*.auto.tfvars.json`, lexical order     | yes (`build run`)      |
| 3 | `terraform.tfvars.json`                                   | no                    |
| 4 | `terraform.tfvars`                                        | no                    |
| 5 | **Environment variables (`TF_VAR_*`)**                    | **yes (`deploy run`)** |
| 6 | The `variable` block's `default`                          | n/a                   |

Row 2 beats row 5. That is the whole bug.

### 5. Scope: every category, not just `properties`/`custom`

The reporter scoped this to `properties`/`custom`. It is not that narrow. The
single `TF_VAR_` delivery block in `deploy_controller.py` covers **all** of
`dns`, `networks`, `firewalls`, `providers`, `tenant`, `topologies`,
`resx_<type>`, `workspace`, `flags`, `variables`, `properties`, `custom` — and
`planned_files()` writes an `*.auto.tfvars.json` for every one of them. Any
token in any of these categories is shadowed identically.

### 6. Why the test suite is green

`tests/strata/controllers/test_deploy_controller.py` asserts the `TF_VAR_*`
env var's JSON content, and separately asserts that the on-disk file was *not*
rewritten (that was the intended guarantee). Nothing in the suite runs a real
`terraform plan` to observe which source actually wins, so the tests pass while
the real behaviour is wrong. Any fix needs a test that closes this specific
gap — asserting on the delivered env var alone cannot detect this class of bug.

## Impact

For `--dry-run` the damage is limited to a wrong plan. For a real apply, the
literal token string propagates into whatever consumes the variable. The
reporter's own case (a resource-naming formula built from `customer_code`) would
either fail Azure's name validation outright on `$`, `{`, `}`, `:`, or silently
produce a corrupt resource name where validation is lenient.

Treat this as a correctness and security issue, not a cosmetic one: the same
mechanism delivers secret-backed values, so a shadowed secret token means a
resource is configured with a literal `"${secret:KEY}"` string rather than
failing loudly.

## Why build-time resolution cannot be the fix

The obvious-looking alternative — have `build run` resolve the tokens so the
file it writes is never stale — is not available. Three independent reasons,
each sufficient on its own:

1. **`build run` makes no network calls by default.**
   `build_value_references()` reads `constant` and `environment` stores only;
   everything else (all secrets, every integration-backed store) is given
   `value=None` deliberately, so it is structurally impossible to populate wrong
   (build-time-value-categories.md Q4/Q5). An integration-backed `${var:}` has
   no value at build time to write.
2. **`build run` must never write a resolved secret to disk.** This is already
   an absolute rule — build-time-value-categories.md Q6/Q7 states resolved
   values "are never written to any file, with or without the flag", including
   under the opt-in `--resolve`. Resolving `${secret:}` into
   `properties.auto.tfvars.json` would break it.
3. **`${output:}` tokens cannot exist at build time at all.** They reference
   another execution step's Terraform outputs, which only exist once that step
   has actually run. They are per-step and deploy-time by definition.

A partial build-time fix (resolve only the purely-local `${value:}` kind, leave
the rest) would reintroduce exactly the curated-allowlist shape that gap #17 was
raised to remove, and would leave the bug live for every other token kind. Not
viable.

## Why rewriting the file is the only remaining option

There are exactly three channels by which a value can reach Terraform, and each
is independently constrained:

| Channel                      | Precedence  | Secret-safe?                                                    |
| ---------------------------- | ----------- | --------------------------------------------------------------- |
| `-var` CLI flag              | highest     | **No** — leaks via `ps`/process listing. Explicitly forbidden by [terraform.py](../../src/strata/integrations/terraform.py#L9)'s v1-evidenced rule. |
| `*.auto.tfvars.json` on disk | beats env   | **No** — a resolved secret must never be written to disk.         |
| `TF_VAR_*` env var           | **lowest**  | Yes — this is why strata chose it.                               |

No single channel is both secret-safe and higher-precedence than the on-disk
file. So the fix cannot be "deliver the resolved value through a stronger
channel" — no such channel exists.

The fix has to come from the other direction: **make sure the stale on-disk file
does not declare the conflicting variable at all.** Once the file is silent
about a variable, the `TF_VAR_` env var becomes that variable's only source and
wins by default, regardless of its low precedence.

That reframing is what makes a deploy-time rewrite of the file not merely the
best option but the only one.

## Proposed design

At deploy time, per step, immediately before invoking Terraform, rewrite each
`*.auto.tfvars.json` file that `build run` already wrote into that step's
directory, driven by the same already-resolved payloads the `TF_VAR_` block
uses today. Per **top-level Terraform variable key**:

- **No secret-shaped leaf anywhere beneath it** — write the fully resolved value
  into the file. The file legitimately wins; its content is now correct.
- **Any secret-shaped leaf beneath it** — omit the key from the file entirely,
  and deliver the whole resolved variable via `TF_VAR_<key>` exactly as today.
  The env var is now uncontested, so it applies. No resolved secret ever
  reaches disk.

"Secret-shaped" reuses the existing rule unchanged: a leaf containing any
`${secret:...}` token is secret-shaped as a whole, even when mixed with
`${var:}`/`${feature:}` in one string.

The per-key granularity matters: a `TF_VAR_` env var carries an entire Terraform
variable as one JSON document, so a secret nested deep inside a complex variable
cannot be delivered on its own. Omitting the whole top-level key and delivering
the whole variable through the env var is what keeps the two channels from
fighting.

### Primitives that already exist

`resolve_value_tokens_tracking_secrets()`
([value_tokens.py](../../src/strata/utils/value_tokens.py#L244)) already
resolves non-secret leaves in place and reports secret-shaped leaves separately
by dotted path. Terraform skipped it because env-var delivery made the split
unnecessary. This design is precisely the case it was built for — the dotted
path's first segment is the top-level variable key, which is the exact
granularity needed above. No new resolution primitive is required.

`planned_files()`'s `_REAL_VARIABLE_NAME` wrapping and `FLAT_CATEGORIES`
per-key flattening already define the file-shape-to-variable-name mapping. The
rewrite must reuse it rather than re-derive it, or the two will drift.

### Boundaries this does not cross

- **ADR-0025 is unaffected.** Its rule is "never rewrite a *synced source*
  file". `*.auto.tfvars.json` is strata's own generated build output, not a
  synced source. Helm and Compose already rewrite their own build-written output
  in place at deploy time for the same reason; this brings Terraform in line
  with them rather than inventing a new pattern.
- **ADR-0022 D4's render-vs-execute split survives**, but only if the rewrite is
  strictly a value substitution into an existing file. The rule should be:
  rewrite a file only where `build run` already wrote one; never create a file
  `build run` did not produce. If no file exists, the env vars are already
  uncontested and the bug does not arise.
- **The "secrets never touch disk" guarantee is preserved exactly**, by
  construction, via the omit-the-key rule.

## Phased implementation plan

- [ ] **Phase 1 — Reproduce as a failing test.** A test that invokes a real
      `terraform` binary (or a faithful stand-in that honours precedence) against
      a directory containing both a token-bearing `*.auto.tfvars.json` and the
      corresponding `TF_VAR_`, and asserts the *effective* value. Must fail
      today. Without this the fix cannot be proven, since the existing tests
      cannot observe precedence at all.
- [ ] **Phase 2 — Rewrite for non-secret values.** Extract the file-shape
      mapping from `planned_files()` into something both build and deploy call,
      then rewrite each existing per-step `*.auto.tfvars.json` from the resolved
      payload. Covers every category at once, not a curated subset.
- [ ] **Phase 3 — Secret-shaped keys.** Switch the rewrite to
      `resolve_value_tokens_tracking_secrets()`, omit any top-level key with a
      secret-shaped leaf beneath it, and confirm the `TF_VAR_` fallback applies.
      Add a test asserting no resolved secret appears anywhere in the step
      directory after a deploy.
- [ ] **Phase 4 — Correct the design docs.** The inverted precedence claim in
      `value-token-resolution.md` and `terraform-tfvars-parity.md` must be fixed
      where it is stated, not merely contradicted elsewhere; likewise the
      "an env-var-sourced value overrides the same-named `-var-file` entry"
      comment in `deploy_controller.py`.
- [ ] **Phase 5 — ADR.** Record the precedence finding, the three-channel
      constraint matrix, and the secrets-on-disk boundary. Then graduate this
      doc to `docs/design/`.

Each phase lands as its own change with the full check suite clean, per this
repo's convention.

## Remaining Work / Open Questions

1. **`--dry-run` must rewrite too.** `dry_run` runs a real
   `init`/`validate`/`plan` (it is not a zero-contact preview — that is
   `smoke_test`), so it needs correct values. `smoke_test` makes no tool contact
   and should almost certainly not touch the files at all. Confirm both.
2. **Build artifacts stop being a pure function of `build run`.** After this
   change, a step directory's contents depend on the deploy that last ran
   against it. The rewrite is derived from the in-memory graph each time, never
   read back from the file, so it is idempotent — but the provenance/audit
   implications need a decision, and `docs/work/audit-trail.md` may need a note.
3. **Should the env var still be set for non-secret keys?** Setting both is
   harmless (the file wins and both now carry the same resolved value) and keeps
   one code path. Leaving it also preserves correct behaviour when no file
   exists. Leaning yes, but it should be a stated decision rather than an
   accident.
4. **Is a `terraform`-binary-dependent test acceptable in CI?** If not, Phase 1
   needs a different mechanism that still genuinely observes precedence rather
   than asserting on strata's inputs.
5. **Audit whether any other integration makes the same precedence assumption.**
   Helm and Compose rewrite their own output already, so they appear safe, but
   this was a design-level error and deserves an explicit check rather than an
   assumption.
6. **Does anything legitimately rely on the current shadowing?** A workspace
   that happens to depend on the build-time literal would change behaviour. No
   such case is known; worth a scan of `config/` and the real consumers before
   Phase 2 lands.

## Related Decisions

- [ADR-0022](../decisions/0022-strata-build-run.md) — D4's render-vs-execute
  split, which puts both commands in the same directory.
- [ADR-0025](../decisions/0025-strata-supplies-input-not-source-rewriting.md) —
  strata supplies input rather than rewriting sources; scoped to synced sources,
  so it permits rewriting strata's own output.
- [ADR-0027](../decisions/0027-strata-deploy-run.md) — `deploy run`'s execution
  model.
- [terraform-tfvars-parity.md](../design/terraform-tfvars-parity.md) — defines
  the `TF_VAR_` delivery and naming; contains the inverted precedence claim.
- [value-token-resolution.md](../design/value-token-resolution.md) — the
  deploy-time resolver design; also states the inverted claim.
- [build-time-value-categories.md](../design/build-time-value-categories.md) —
  Q4/Q5/Q6/Q7, the build-time no-network/no-secrets rules that rule out a
  build-time fix.

## Changelog

- 2026-10-07: Created from an external bug report. Verified every claim against
  this repo's source and Terraform's official docs; confirmed the report and
  widened its scope from `properties`/`custom` to all twelve projected
  categories. Established that the root cause is a design-level inverted
  precedence assumption recorded in two design docs, not a local coding slip.
  Ruled out build-time resolution on three independent grounds, and established
  via the three-channel constraint matrix that a deploy-time rewrite is the only
  remaining option. Drafted the omit-secret-shaped-keys design and a five-phase
  plan.
