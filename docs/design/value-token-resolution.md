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
