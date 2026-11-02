# Remove Configuration's Unused `properties`/`configuration`/`custom` Fields

- Status: accepted
- Date: 2026-10-13
- Related: [ADR-0017](0017-tags-labels-configuration-custom-placement.md)
  (Decision 5 enumerates every kind that deliberately gained
  `configuration`/`custom` — `Configuration` is not among them, confirming
  these fields were never a deliberate placement decision for this kind),
  [ADR-0024](0024-tenant-defaults-merge.md) (the real, wired mechanism —
  an Environment referenced solution-wide — that already serves the
  "values meant to reach the whole system" need these fields look like
  they were meant for), [ADR-0037](0037-flatten-provider-resource-module-properties.md)
  (a related but independent decision from the same work doc — flattening
  Provider/Resource/Module's `properties`; split into its own ADR per the
  one-decision-per-ADR rule, since Configuration's problem and reasoning
  are unrelated: dead-code removal, not a naming collision),
  [flatten-provider-resource-module-properties.md](../design/flatten-provider-resource-module-properties.md)
  (the work doc this was found in)

## Context and Problem Statement

`ConfigurationSpecModel` carries `properties`, `configuration`, and
`custom` (all plain `dict[str, Any] | None`). Checked directly against
real code rather than assumed: **zero references** to
`configuration.spec.properties`/`.configuration`/`.custom` exist anywhere
in `src/` outside the model's own field declaration — nothing reads,
merges, or passes any of the three through to a builder. This repo's own
dogfooding solution (`config/configuration.yaml`) sets none of the three
either.

No ADR ever deliberately decided to add these fields to `Configuration`.
ADR-0017 (the ADR that actually decided, kind-by-kind, where
`configuration`/`custom` should and shouldn't exist) explicitly lists
every kind that gained them — Firewall, Dns (per zone), Network (per
network), Namespace, Module, Integration — and `Configuration` is not on
that list. The most likely explanation is that these fields were copied
unexamined from the Provider/Resource-style `properties`/`configuration`/
`custom` three-way split (ADR-0003) onto `Configuration` without a
specific justification, since `kind: configuration` is the one document
every workspace in a solution implicitly shares — which makes it look
like a natural place to park "values meant for the whole system."

That assumption doesn't hold up against the mechanism that actually does
this job, and does it well: an **Environment** document. Nothing stops
the same Environment being referenced from every Tenant's/Deployment's
`environments` list solution-wide — `variables`/`secrets`/`features`
stores plus `properties`/`custom` on `EnvironmentSpecModel` already merge
in a well-defined order (Tenant → Environment → Deployment, ADR-0024). A
solution-wide "globals" file is already just a naming convention
(reference the same Environment name everywhere that needs it), not a
field `Configuration` needs to grow a second, competing, unconsumed copy
of.

## Considered Options

- **Keep all three as-is** (status quo). Rejected — dead weight with no
  consumer today and no concrete plan to ever give them one; schema
  surface that looks load-bearing but isn't.
- **Flatten/rename, matching ADR-0037's treatment of Provider/Resource/
  Module.** Rejected — doesn't apply. ADR-0037's problem is a *naming
  collision* between two different concepts sharing the word `properties`
  on *different* kinds; `Configuration`'s `properties` doesn't collide
  with anything on `Configuration` itself, and it isn't even a typed
  sub-model (already a plain dict) — there is no wrapper to flatten.
- **Remove all three fields entirely** (chosen). No replacement field
  added.

## Decision Outcome

Chosen: remove `properties`, `configuration`, and `custom` from
`ConfigurationSpecModel` entirely, with no replacement. `Configuration`'s
real job — provider/topology type registries, security allow-lists, path
conventions, audit config, CVE suppression list — doesn't need a
free-form values channel; the solution-wide-values need these fields
looked like they were for is already met, better, by a shared Environment
document (named, mergeable, well-ordered) per ADR-0024's existing
mechanism.

## Consequences

- **Breaking schema change for any document that happens to set one of
  the three** — none found in this repo's own dogfooding solution; any
  real consumer repo's `configuration.yaml` needs auditing before this
  lands, same as any other breaking v2 schema change.
- **No source-code migration needed beyond the model file itself** (field
  declarations, its docstring, and its own tests) — confirmed zero real
  consumers exist today. Re-verified as a final gate immediately before
  merging, per [flatten-provider-resource-module-properties.md](../design/flatten-provider-resource-module-properties.md)'s
  Implementation Plan Phase 4, not just trusting this design-time finding.
