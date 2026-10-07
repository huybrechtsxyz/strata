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

| #   | Source                                                | Used by strata         |
| --- | ----------------------------------------------------- | ---------------------- |
| 1   | `-var` / `-var-file` on the CLI, and HCP Terraform    | yes (non-secrets only) |
| 2   | `*.auto.tfvars` / `*.auto.tfvars.json`, lexical order | yes (`build run`)      |
| 3   | `terraform.tfvars.json`                               | no                     |
| 4   | `terraform.tfvars`                                    | no                     |
| 5   | **Environment variables (`TF_VAR_*`)**                | **yes (`deploy run`)** |
| 6   | The `variable` block's `default`                      | n/a                    |

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

| Channel                      | Precedence | Secret-safe?                                                                                                                                        |
| ---------------------------- | ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `-var` CLI flag              | highest    | **No** — leaks via `ps`/process listing. Explicitly forbidden by [terraform.py](../../src/strata/integrations/terraform.py#L9)'s v1-evidenced rule. |
| `*.auto.tfvars.json` on disk | beats env  | **No** — a resolved secret must never be written to disk.                                                                                           |
| `TF_VAR_*` env var           | **lowest** | Yes — this is why strata chose it.                                                                                                                  |

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

At deploy time, per step, immediately before invoking Terraform, rewrite every
`*.auto.tfvars.json` file `build run` already wrote into that step's directory
(`path`, the same variable the existing `init()`/`plan()`/`deploy()` calls use),
driven by the exact same per-step payloads the `TF_VAR_` block already computes
([deploy_controller.py](../../src/strata/controllers/deploy_controller.py#L635)).
**The file must always be rewritten, never conditionally skipped** — a category
that resolves secret-free must get its full resolved content; a category with a
secret anywhere inside it must get its file content actively replaced with
`{}`, not left alone, or the stale literal token would still be sitting there
from `build run`.

Per category, the rule differs by shape — there are four distinct shapes
already established by `planned_files()`/`FLAT_CATEGORIES`, not one uniform
case:

1. **`FLAT_CATEGORIES`** (`workspace`/`flags`/`variables`/`properties`/
   `custom`) — the file is a bare `{key: value}` map; each key is its own
   independent real Terraform variable. Resolve with
   `resolve_value_tokens_tracking_secrets(payload, tokens)`, take the first
   dotted-path segment of each reported secret as its owning top-level key, and
   write every key **except** the tainted ones. A tainted key keeps being
   delivered by `TF_VAR_<key>` exactly as today — now uncontested, since the
   file no longer declares it.
2. **Single-variable broadcast categories** (`providers`, `tenant`,
   `topologies`, `namespaces`) — the file wraps one dict under one real
   variable name (`real_variable_name(category)`). Resolve with the same
   function; if it reports *any* secret anywhere, write `{}` (the whole
   variable is undeclared by the file, delivered by `TF_VAR_<name>` instead);
   otherwise write `{real_variable_name(category): resolved}`.
3. **Claimed/broadcast document categories** (`dns`, `networks`, `firewalls`) —
   identical to (2), except the payload resolved is this step's own
   `docs_for_step` (the exact dict already built for this step's env-var
   delivery, same claiming/broadcast rules, same `${output:}` scoping), not the
   workspace-wide payload. **Decision:** the rewritten file reflects only what
   this step is entitled to see — a document claimed by a *different* step is
   dropped from this step's file. This exactly matches what that step's own
   `TF_VAR_<name>` env var already carries today; file and env var now agree
   for the first time, instead of the file silently carrying a broader,
   un-scoped set underneath. No step's own Terraform module was evidenced to
   read another step's claimed document anyway (each step is its own synced
   source / root module).
4. **`resx_<type>`** — always write `{}`, unconditionally, regardless of
   secrets. Reasoning in its own subsection below; this is simpler than (2),
   not a variant of it.

### Why `resx_<type>` always writes `{}`, never a resolved value

`deploy_controller.py` already merges every `resx_<type>` category into one
combined `TF_VAR_resources` env var, because every type is actually the same
Terraform variable, `resources` (confirmed against v1's real
`_build_resources_by_category()`). But `build run` still writes one **file per
type** (`resx_server.auto.tfvars.json`, `resx_storage.auto.tfvars.json`, …),
each independently declaring that same `resources` variable in full.

If this design's per-category rewrite rule were applied to each `resx_<type>`
file independently (rewrite with its own resolved value when secret-free), two
or more resource types present together would leave multiple files each
re-declaring the whole `resources` variable — the same one-variable,
multiple-conflicting-declarations problem this whole investigation is about,
just moved one level down and self-inflicted. There is no way to represent
"several files jointly contribute to one merged variable" as independent
per-file content.

So `resx_<type>` is not folded into case (2) above — every `resx_<type>.auto.tfvars.json`
is unconditionally blanked at deploy time, for every step, and `TF_VAR_resources`
(already computed exactly this way today) is the sole source of truth. This is
strictly simpler than per-file resolution and sidesteps the multi-file
collision question entirely rather than needing to answer it.

### Primitives that already exist — no new resolution algorithm needed

`resolve_value_tokens_tracking_secrets()`
([value_tokens.py](../../src/strata/utils/value_tokens.py#L244)) already
resolves non-secret leaves in place and reports secret-shaped leaves separately
by dotted path — built for Helm's `--set-string <path>=<value>`
([helm.py](../../src/strata/integrations/helm.py#L397)'s `resolve_module_values()`
is the existing precedent to mirror: same function, same split, just a
different consumer of the `secrets` half). The dotted path's first segment is
exactly the top-level variable key this design needs. Terraform's own
`resolve_value_tokens_in_mapping()` calls stay as they are — this is an
additional file-write step alongside them, not a replacement.

### New code surface

One new function, next to `planned_files()` in
[terraform_projection.py](../../src/strata/integrations/terraform_projection.py#L703)
(same module already owning the file-shape/naming conventions,
`_REAL_VARIABLE_NAME`/`FLAT_CATEGORIES`, so the two can't drift apart):

```python
def resolve_deploy_time_files(
    payloads: dict[str, Any], tokens: Mapping[str, str]
) -> list[tuple[str, dict[str, Any]]]:
    """Deploy-time counterpart to planned_files(): same (filename, data)
    shape, but every file is always emitted (never skipped when empty —
    an empty result here must actively blank a stale build-time file, not
    leave it alone) and every leaf is resolved/secret-split per this doc's
    four category shapes."""
```

Called once per step from `deploy_controller.py`'s existing loop, right where
`configuration_payloads`/`docs_for_step` are already in scope — merge
`docs_for_step` (keyed by its three category names) over `configuration_payloads`
before the call, so the dns/networks/firewalls entries use the step-scoped
payload while everything else uses the broadcast one:

```python
per_step_payloads = {**configuration_payloads, **docs_for_step_by_category}
for filename, data in resolve_deploy_time_files(per_step_payloads, tokens):
    (path / filename).write_text(json.dumps(data))
```

placed immediately before the existing `TF_VAR_` env var assembly block (order
doesn't matter functionally — the file and the env var are now independent,
non-conflicting sources by construction — but doing the file write first keeps
the diff next to the data it mirrors).

### Boundaries this does not cross

- **ADR-0025 is unaffected.** Its rule is "never rewrite a *synced source*
  file". `*.auto.tfvars.json` is strata's own generated build output, not a
  synced source. Helm and Compose already rewrite their own build-written output
  in place at deploy time for the same reason; this brings Terraform in line
  with them rather than inventing a new pattern.
- **ADR-0022 D4's render-vs-execute split survives**, but only if the rewrite is
  strictly a value substitution into an existing file. The rule is: rewrite a
  file only where `build run` already wrote one (iterate `payloads`, which only
  ever contains categories `build_configuration_payloads()`/
  `build_dns_networks_firewalls_payloads()` already populate); never create a
  file `build run` did not produce.
- **The "secrets never touch disk" guarantee is preserved exactly**, by
  construction, via the omit-the-key / blank-the-file rules above.

## Phased implementation plan

- [ ] **Phase 1 — Reproduce as a failing test.** A test that invokes a real
      `terraform` binary (or a faithful stand-in that genuinely honours
      precedence, see Open Question 4) against a directory containing both a
      token-bearing `*.auto.tfvars.json` and the corresponding `TF_VAR_`, and
      asserts the *effective* value. Must fail today. Without this the fix
      cannot be proven, since the existing tests only assert on strata's own
      inputs (the env var's content, and that the file was untouched) and
      cannot observe precedence at all.
- [ ] **Phase 2 — `resolve_deploy_time_files()` for non-secret categories.**
      Add the function (above) to `terraform_projection.py`, covering shapes
      1-3 (FLAT per-key, single-variable broadcast, claimed/broadcast
      documents); wire the per-step call site into `deploy_controller.py`.
      Reuses `resolve_value_tokens_tracking_secrets()` as-is. Covers every
      affected category in one phase, not a curated subset — the whole point
      of this bug was a curated-allowlist-shaped gap (gap #17) re-appearing;
      don't reintroduce that shape here.
- [ ] **Phase 3 — `resx_<type>` always-blank.** Smallest phase: unconditionally
      write `{}` for every `resx_<type>.auto.tfvars.json` this step's
      `configuration_payloads` names. Add the test from Phase 1's harness
      confirming `TF_VAR_resources` alone determines the effective value when
      two or more resource types are present together.
- [ ] **Phase 4 — Secret coverage test.** Add a test (can reuse Phase 1's
      terraform-observing harness) with a `${secret:}` token in each of a
      `FLAT_CATEGORIES` key, a single-variable broadcast category, and a
      claimed document — assert the effective Terraform value is the resolved
      secret in every case, and separately assert no resolved secret value
      appears anywhere in the step directory's files on disk.
- [ ] **Phase 5 — Correct the design docs and code comments.** The inverted
      precedence claim must be fixed where it is stated, not merely
      contradicted elsewhere: `value-token-resolution.md`'s "an env-var-sourced
      value overrides the same-named `-var-file` entry", the identical phrase
      in `terraform-tfvars-parity.md`, and the matching comment in
      `deploy_controller.py` above the `TF_VAR_` delivery block.
- [ ] **Phase 6 — ADR.** Record the precedence finding, the three-channel
      constraint matrix (CLI/file/env, none both secret-safe and
      highest-precedence), the `resx_<type>` always-blank decision, and the
      claimed-document scoping decision. Then graduate this doc to
      `docs/design/`.

Each phase lands as its own change with the full check suite clean
(`mypy`/`ruff`/`pytest`/`lint-imports`), per this repo's convention.

## Remaining Work / Open Questions

1. **`--dry-run` must rewrite too.** `dry_run` runs a real
   `init`/`validate`/`plan` (it is not a zero-contact preview — that is
   `smoke_test`), so it needs correct values. `smoke_test` makes no tool contact
   at all and must not touch the files. The proposed call site (inside the
   existing step loop, gated the same way the `TF_VAR_` env assembly already
   is) gets this for free — confirm it in Phase 2's test, don't just assume it.
2. **Build artifacts stop being a pure function of `build run`.** After this
   change, a step directory's `*.auto.tfvars.json` contents depend on the
   deploy that last ran against it, not solely on the last build. The rewrite
   is derived from the in-memory graph each time, never read back from the
   file, so it is idempotent across repeated deploys — but the
   provenance/audit implications need a decision, and
   `docs/work/audit-trail.md` may need a note.
3. **Should the env var still be set for non-secret keys?** Setting both is
   harmless (the file wins and both now carry the same resolved value) and
   keeps one code path — no branching on whether a key turned out secret-free.
   Leaning yes, as the simplest option, but it should be a stated decision
   rather than an accident of not bothering to remove it.
4. **Is a `terraform`-binary-dependent test acceptable in CI?** If not, Phase 1
   needs a different mechanism that still genuinely observes precedence (e.g.
   a minimal fake binary that implements just enough of the precedence rules
   to be a faithful stand-in) rather than asserting on strata's inputs, which
   is exactly the gap that let this bug ship.
5. **Audit whether any other integration makes the same precedence assumption.**
   Helm and Compose rewrite their own build-written output already
   (`helm.py`/`compose.py`), so they appear safe by construction — but this was
   a design-level error, found in exactly the two design docs that describe
   the mechanism, and deserves an explicit check rather than an assumption
   that "the other two are fine because they look different."
6. **Does anything legitimately rely on the current shadowing?** Scanned
   `config/` (this repo's own dogfooding solution) directly: no case found
   expecting the stale literal to survive. Two real, currently-broken examples
   confirm the fix's value instead — `resources/networking.yaml`'s
   `key_vault_secret_id: "${secret:APPGW_CERT_SECRET_ID}"` (a `resx_<type>`
   category, case 4 above) and `dns/example.yaml`'s
   `value: "${var:PUBLIC_IP}"` (a claimed/broadcast document, case 3). Neither
   should be treated as a newly-discovered regression risk; both are instances
   of the live bug this doc tracks. No further scan needed before Phase 2.

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
- 2026-10-07: Confirmed rewrite as the chosen direction. Replaced the design
  with a fully concrete, code-referenced version: four category shapes
  (`FLAT_CATEGORIES` per-key, single-variable broadcast, claimed/broadcast
  documents, `resx_<type>`), one new function
  (`resolve_deploy_time_files()` in `terraform_projection.py`) and its exact
  call site in `deploy_controller.py`'s existing step loop. Resolved the
  `resx_<type>` multi-file-same-variable question (always blank every
  `resx_<type>` file, rely solely on the already-existing merged
  `TF_VAR_resources`) rather than leaving it open. Expanded five phases to
  six, splitting the test work into its own reproduction phase (1) and a
  dedicated secret-coverage phase (4) so the fix can't be declared done on
  the non-secret path alone. Scanned `config/` for Open Question 6 directly
  — found no case relying on the current shadowing, and two real examples
  (`networking.yaml`'s `key_vault_secret_id`, `dns/example.yaml`'s
  `PUBLIC_IP`) that are themselves live instances of the bug.
