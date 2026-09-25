# Value Token Resolution — Design

- Status: partially-implemented
- Last updated: 2026-09-25

## Overview

The unified `${var:KEY}`/`${secret:KEY}`/`${feature:KEY}` embedded-token
mechanism, adopted in [ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md)
to replace v1's per-kind `value`/`var`/`secret` discriminated unions
(`ValueSourceModel`, superseded). This doc tracks the mechanism's current
state across kinds so the same "Environment cross-check deferred" note
doesn't need repeating in every kind's ADR.

## Value Supply Mechanisms — three distinct ways, not one (2026-09-25)

Found while investigating `output.template` (ADR-0023 D3) as a candidate
`build run` feature: strata has **three** genuinely different mechanisms
for getting a value into generated output, easy to conflate since all
three ultimately draw from the same resolved variables/secrets/features.
Keeping them distinct, not merging them, is the point of this section.

**A. Structured input files/env vars** — `.auto.tfvars.json`, Helm
`values.yaml`, Compose environment vars; `TF_VAR_*`/`--set-string` for
secrets. The **preferred** mechanism (ADR-0025's own reasoning for why
strata never rewrites a synced source file in place): tool-native, the
consuming tool (Terraform/Helm/Compose) interprets it itself, zero custom
resolution code needed. Build-time-safe half (`constant`/`environment`
values) is built (docs/design/build-time-value-categories.md); the
deploy-time half (secrets/integration-backed) is not, blocked on
`deploy run`.

**B. Embedded string tokens** — `${var:KEY}`/`${secret:KEY}`/`${feature:KEY}`,
this doc's own subject. Use this **inside a field strata's own schema
defines** (`DnsRecordModel.value`, `SubnetModel.cidr`,
`FirewallRuleModel.from_`/`.to`, `ModuleServiceEnvironmentModel.value`,
`ProvisionerBackendModel.configuration`) — a partial substitution within
one field's own string value, for values that need to live inside a
document *strata itself owns*, not a third-party tool's native config
(that's what (A) is for). Always deploy-time (confirmed directly in v1:
`resolve_expr_string()`/`EXPR_PATTERN` is used exclusively by
`TerraformDeployer`/`HelmDeployer`, never any build-time builder).

**C. Jinja2 full-file templates** — `output.template` (ADR-0023 D3). Use
this when **generating/templating an entire file** strata's own default
projection doesn't produce — a different problem from (B): whole-file
generation, not one field's value. Build-time can only validate such a
template (statically check its referenced names exist somewhere in the
declared schema, via `jinja2.meta.find_undeclared_variables()` — no
rendering); the actual render is deploy-time only, same reason as (B) —
full values (including secret-shaped-leaf awareness: never write a secret
to disk) aren't available until then.

**The guiding rule, going forward: strata's own document fields use
embedded tokens (B); generating or templating a whole file uses Jinja2
(C).** Don't reach for Jinja2 to fill in one field of an existing
strata-owned document, and don't reach for embedded tokens to generate an
entire new file from scratch. (B) and (C) are not redundant with each
other and should not be merged into one mechanism — but they share the
same underlying need (fully-resolved values, secret-shaped-leaf safety),
so when the deploy layer is designed, both should be built on top of one
shared resolution primitive (matching v1's real `ResolvedValues`), not as
two independent re-implementations of "resolve a value, refuse to write a
secret."

## Current Design

- **Syntax**: `${kind:KEY}` where `kind` is `var`, `secret`, or `feature`
  (future: `step`, once [Context](provisioning-injection-model.md) lands).
  Embeddable anywhere inside a plain `str` field — a literal is just a
  string with no tokens in it.
- `validate_value_tokens()` (`common_models.py`) — Phase 1 syntax check only
  (rejects unknown kind, missing key, missing colon). Cannot check whether
  `KEY` actually exists anywhere — that's Phase 2.
- `has_value_tokens()` — lets a kind skip literal-format checks (e.g. CIDR
  syntax) when a token is present, since the value isn't known until
  resolution.
- The actual **resolver/router** (partial regex substitution across a whole
  leaf; a leaf containing any `${secret:...}` token is secret-shaped and
  routed to a deploy-time-only channel) is designed (ADR-0002 Decision 5)
  but **not implemented** — this is separate from the Phase 1 syntax
  validators above.

### Real-world evidence this is genuinely needed, not just theoretical (2026-09-25)

Checked haven and cfg-deployment directly rather than assuming. haven
has no `kind: dns`/`kind: network` documents at all. cfg-deployment's
only real ones (`stacks/spoke/dns.yaml`/`network.yaml`) use **zero**
`${var:}`/`${secret:}`/`${feature:}` tokens today — every value is a literal
placeholder (`"10.0.0.0/22"`, `"TODO-agw-public-fqdn.placeholder.azure.com"`).
But the file's own comments make clear this is a workaround, not a lack of
need: *"The actual values the deploy reads still live in
`deploy/hubs/z01/s01/environment.yaml`'s `variables`
(`ring_subnet_cidrs`, `apim_subnet_cidr`)... keep this file's CIDRs in sync
with that environment file by hand; nothing wires the two together
automatically."* Someone wants `cidr: "${var:ring_subnet_cidrs}"` but can't
use it — nothing would ever resolve it, so it would just be written
literally into `networks.auto.tfvars.json`, useless to Terraform. Zero
usage reflects zero *capability*, not zero *want*.

(Separately, `dns.yaml`'s own comment flags that `DnsRecordModel.name` has
no Value-token union at all — only `.value` does — so even a working
resolver wouldn't let a shared DNS template parameterize a record's name
per customer. A real, larger gap, but a schema change, not a resolution
change — out of scope here, logged for later.)

### Decision: one resolver for every kind, built at deploy time — not a per-kind build-time band-aid (2026-09-25)

A resolver that only covers `dns`/`network` while `firewall`/`module` (the
other two kinds in the Per-Kind Status table below, same Value-token
mechanism) stay unresolved would be inconsistent — a system like this works
for every kind that has Value-token fields, or it doesn't earn calling
itself "the" resolver. **Do not build a `build run`-scoped, `dns`/`network`-only
fix.** Confirmed directly in v1's real `terraform_builder.py`: `_build_dns_vars()`/
`_build_network_vars()` never call the resolver at all — v1's own real
`resolve_expr_string()`/`EXPR_PATTERN` mechanism (`resolved_values.py`) is
used **only** at deploy time (`TerraformDeployer`'s `backend.configuration`,
`HelmDeployer`'s rendered `values.yaml`), with the deployer's fully-resolved
`ResolvedValues` (every store type, including secrets and integration-backed)
and a **fail-loud** rule (an unresolved reference is always an error). That's
correct there and would be wrong at build time, where only `constant`/
`environment`-backed values exist (docs/design/build-time-value-categories.md,
Q1/Q5) — a `${var:X}` backed by an integration-backed or secret store is
*expected* to stay unresolved at build time, not an error.

**Build the one shared resolver when the deploy/stage layer is designed**
(it needs deploy's own fully-resolved values anyway), applying it uniformly
to every kind's Value-token fields (`dns`, `network`, `firewall`, `module`,
and any future one) — not separately, not build-time-only. Tracked as
blocked on the same prerequisite `docs/design/provisioning-injection-model.md`
already names for Context/deploy-time work generally.

## Per-Kind Status

| Kind | Value-token field(s) | Phase 1 syntax check | Phase 2 Environment cross-check |
| --- | --- | --- | --- |
| Provider | none identified | n/a | n/a |
| Resource | none (`configuration` is raw passthrough, not token-aware) | n/a | n/a |
| DNS | `DnsRecordModel.value` | Done ([ADR-0005](../decisions/0005-dns-model-design-decisions.md)) | Deferred — needs `environment` kind |
| Network | `SubnetModel.cidr`, `NetworkDefinitionModel.address_space` entries | Done ([ADR-0007](../decisions/0007-network-model-design-decisions.md)) | Deferred — needs `environment` kind |
| Firewall | `FirewallRuleModel.from_`/`.to` | Done ([ADR-0008](../decisions/0008-firewall-model-design-decisions.md)) | Deferred — needs `environment` kind |
| Module | `ModuleServiceEnvironmentModel.value` | Done ([ADR-0009](../decisions/0009-module-model-design-decisions.md)) | Deferred — needs `environment` kind |
| Namespace, Topology, Workspace | none yet | n/a | n/a |

## Related Decisions

- [ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md) — token syntax decision, resolver design, rationale for rejecting `ValueSourceModel`/Jinja
- [ADR-0005](../decisions/0005-dns-model-design-decisions.md), [ADR-0007](../decisions/0007-network-model-design-decisions.md), [ADR-0008](../decisions/0008-firewall-model-design-decisions.md), [ADR-0009](../decisions/0009-module-model-design-decisions.md) — per-kind adoption
- [ADR-0006](../decisions/0006-context-shared-stage-runtime-store.md) — Context, the `${step:}` token this doc doesn't cover yet

## Remaining Work / Open Questions

- ~~Build the `environment` kind~~ — **done**, this claim was stale:
  `EnvironmentModel`/`PlatformKind.ENVIRONMENT` already exists and is used
  extensively (`value_controller.py`, `build_value_references()`, etc.).
  What's still open: wire `_validate_dynamic(environment_model=...)` on
  `DnsService`/`NetworkService`/`FirewallService`/`ModuleService` — one
  shared pattern, four call sites (sketched already in ADR-0002's worked
  example).
- Build the actual resolver/router (partial regex substitution + secret-
  shaped-leaf routing) — **decided 2026-09-25: one shared implementation,
  applied uniformly to every kind above, built at deploy time** (see the
  new section above). Still not started — blocked on the deploy/stage
  layer, same as `docs/design/provisioning-injection-model.md`'s own
  blocker.
- `DnsRecordModel.name` has no Value-token union at all (only `.value`
  does) — found 2026-09-25 via real evidence (`cfg-deployment/stacks/
  spoke/dns.yaml`'s own comment). A schema change, not a resolution change;
  logged here, not designed yet.
- Add `${step:step_name.output_key}` as a fourth token kind once Context
  ([ADR-0006](../decisions/0006-context-shared-stage-runtime-store.md)) is
  built, and re-add an output-sourced binding to `DnsRecordModel`.

## Changelog

- 2026-09-24: Created, consolidating the recurring "Environment cross-check
  deferred until `environment` kind exists" remaining-work item duplicated
  across ADR-0005/0007/0008/0009.
- 2026-09-25: Investigated dns/networks token resolution as a candidate
  `build run` feature (docs/design/build-command.md's gap table). Found
  real evidence it's genuinely wanted (cfg-deployment's `network.yaml`/
  `dns.yaml` comments) but confirmed v1 itself never resolves these at
  build time — only at deploy, with fully-resolved values and a fail-loud
  rule. Decided against a `build run`-scoped, `dns`/`network`-only fix;
  recorded the one-shared-resolver-at-deploy-time decision above. Also
  corrected the stale "`environment` kind not built" claim.
- 2026-09-25: Investigated `output.template` (ADR-0023 D3) as a candidate
  `build run` feature. Found the same flaw as the dns/networks finding
  above: ADR-0023's Phase 4 sketch assumed full build-time rendering, but
  `variables`/`flags` only carry `constant`/`environment`-backed values at
  build time — any real template referencing a Vault/AppConfig-backed key
  would raise unconditionally, every build. Split into build-time
  validation (genuinely buildable, not yet implemented) and deploy-time
  rendering (blocked on `deploy run`, same as (B) above). This led to
  naming and documenting all three of strata's value-supply mechanisms
  (A/B/C above) explicitly, since (B) and (C) were at risk of being
  conflated or merged — they solve different problems and should stay
  separate, but share one resolution primitive once deploy exists.
- 2026-09-25: **Implemented the build-time-validation half of `output.template`.**
  Added `jinja2` as a dependency; `OutputModel`/`ProvisionerModel.output`
  (valid for any tool, unlike `backend`/`properties`); `strata/utils/templater.py`'s
  `validate_template_references()` — static-only, no rendering, two tiers
  (unknown root name via `jinja2.meta.find_undeclared_variables()`; unknown
  `variables.KEY`/`flags.KEY`/`secrets.KEY` via an AST walk for `Getattr`/
  `Getitem` nodes; `properties`/`custom` skipped, arbitrary shape). Wired
  into `InfraIntegration.prepare()`: when `provisioner.output.template` is
  set, `default_output()` is skipped entirely and nothing is written for
  that provisioner — validated only, deploy renders later. `template_path`
  is resolved by `build_controller.py` (workspace-relative, no `@repo/`
  yet — no evidenced need) and passed through explicitly, matching
  `sync_source()`'s "controller resolves paths, integration consumes
  already-resolved ones" split (ADR-0021 D2) — `templater.py` itself has no
  dependency on `ResolvedWorkspaceGraph`/`strata.integrations` (layering,
  ADR-0003), callers build the `known_names` schema and pass plain
  `dict`/`set` data in. 24 new tests (9 unit, 3 `prepare()`-level, 3
  end-to-end through `build_run()`, plus model tests). Full check suite
  green (1036 tests, mypy 103 files, 0 broken import-linter contracts).
- 2026-09-25: **Review pass found a real gap**: `--dry-run` skipped
  `output.template` validation entirely (it lived inside `sync_source()`'s
  branch, past the `if dry_run: ...; continue` early exit) — contradicting
  `build_run()`'s own documented rule that a dry run still catches what it
  cheaply can (already true for `--resolve`'s validation). Fixed:
  `template_path` resolution and the validation call now happen before the
  `dry_run` branch, so a bad reference or a missing template file is
  caught under `--dry-run` too, with zero filesystem mutation. 2 new tests
  confirm this. Full check suite green (1038 tests).
