# Promotion — Work

- Status: draft
- Last updated: 2026-10-05

## Overview

`deployment_model.py`'s own usage census (15 real deployment documents across
two production repos) found **zero** real use of `versions`, `promotion`, or
`gates` — the same "0 uses" treatment `environment_model.py`'s unused
subtree got. The repo's own v1 archive agrees: "No promotion strategy is
currently adopted." [v2-schema-overview.md](../design/v2-schema-overview.md)
still lists `gates`/`promotion` as undesigned, and
[ADR-0019](../decisions/0019-version-pinning.md) explicitly deferred them:
"Ring/promotion is a planned follow-up for rollout automation... Real
trigger to revisit: a second `kind: version` document actually appearing in
a real repo." This doc is that follow-up design — not because the trigger
fired, but because the shape is now needed to reason about rollout at
~250-customer scale, and it's cheap to design before the next version
document gets written.

v1 built the machinery in full (`promotion_model.py`,
`promotion_record_model.py` + a service, for history) before anyone adopted
it. This doc deliberately does the opposite: design the smallest schema that
answers "where is version X right now?", ship that as a read-only command
first, and only add enforcement/history if real demand shows up.

## Current Design / Progress

### Why v1 was confusing — 7 concepts for one idea

| Concept                                     | Problem                                                                                                               |
| ------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `ProgressionRingEnvironmentModel.wave: int` | Two wave systems: intra-ring integer waves...                                                                         |
| `PromotionWaveModel` (named, on strategy)   | ...and separately, named ordered waves on the strategy. Both schedule rollout.                                        |
| `progression` + `strategy` as 2 kinds       | A pointer between them; strategy adds almost nothing once its other fields go.                                        |
| `type: remote\|helm_chart\|image\|module`   | Duplicates what `pins.{images,charts,remotes,artifacts}` already says.                                                |
| `gates.require_progression_order: bool`     | Opt-in enforcement of the thing you just declared. Why declare an order and not enforce it?                           |
| `scope: tenant`                             | A third targeting axis, on top of ring + wave.                                                                        |
| `versions_path` + `@repo/` syntax           | Path indirection, separate from everything else's name-based addressing (ADR-0015 is identity-based, not path-based). |

Plus a whole `promotion_record_model.py` + service just for history.

### The cut-down: one kind, three concepts (ring, order, wave)

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: promotion
meta:
  name: dispatcher
spec:
  workspace: dispatcher # every deployment this promotion tracks runs this workspace
  rings:
    - name: dev
    - name: tst
      require: any_one
    - name: prd
      require: all
```

Dropped vs. v1, confirmed still unjustified by any evidence:

- `type` — pins already say it (`VersionPinsModel`'s `images`/`charts`/
  `remotes`/`artifacts` categories).
- `strategy` as a separate kind — nothing is left in it once waves move
  (below) and `type` is dropped; a pointer between two kinds for no
  remaining content.
- `require_progression_order: bool` — always on. A declared ring order
  that isn't enforced isn't an order.
- `scope: tenant` — no evidence of a real need for a third targeting axis
  distinct from ring + workspace.
- `require_digests: bool` — v2 has no digest field anywhere yet
  (confirmed: `VersionPinModel` has no digest/sha field); needs the field
  before the gate.
- Promotion records — git history of the version files already is the
  record; no new persisted kind for it.

One correction from the v1-literal port: v1's rings listed *environments*;
in v2 the thing carrying `spec.version` is the **deployment**
(`DeploymentSpecModel.version`), so ring membership must resolve to
deployments, not environments. See the selector below for how that
resolves without hand-listing them.

### Ring membership without enumeration

The original draft of this cut-down still listed every deployment by name
under each ring:

```yaml
# what this doc replaces — don't do this at 250-customer scale
rings:
  - name: prd
    deployments: [dispatcher-c0224-prd, dispatcher-c0062-prd, dispatcher-c0064-prd]
```

That's exactly the enumeration burden the "ship visibility first" framing is
supposed to avoid — every new customer deployment would require an edit to
this file just to show up in `promote status`, on top of the deployment
document itself. v2 already has the two fields needed to compute membership
without listing anything: `DeploymentSpecModel.workspace` (which product) and
`DeploymentSpecModel.environments` (which tier). A ring's membership is just:

> every validated `kind: deployment` document where
> `spec.workspace == promotion.spec.workspace` **and**
> `ring.name in spec.environments` (or `ring.environment`, if set, for the
> rare case where a ring's display name and its matching environment name
> diverge — e.g. a ring named `prd-eu` matching `environment: prd`).

No new field on `Deployment`, no list to maintain on `Promotion` — adding
customer #251 is just adding its deployment document (which has to exist
anyway); it shows up in `promote status` for free the next time the
command runs.

### Wave, relocated — not enumerated, not dropped

v1's wave concept (an integer that schedules rollout order within a ring) is
real and worth keeping — the running example ("sandbox bakes first") is a
genuine, common rollout need. What made it heavy in v1 was pairing it with
a list the operator had to maintain by hand. Since ring membership is now
computed from the deployment's own `workspace`/`environments` fields, the
wave number needs a home that is *also* not a hand-maintained list keyed by
deployment name — and v2 already has exactly one real per-customer document
that's visited once per customer, not once per rollout: `Tenant`.

```yaml
# customers/c0224/tenant.yaml — the sandbox/pilot account
apiVersion: strata.huybrechts.xyz/v2
kind: tenant
meta:
  name: c0224
spec:
  display_name: "Sandbox"
  wave: 1 # bakes first; every tenant without this field rolls out together, after wave 1 succeeds
```

`TenantSpecModel.wave: int | None = None` — optional, and left unset for the
overwhelming majority of tenants. Unset means "general population": those
deployments roll out together, once every explicitly-numbered wave has
succeeded. Only the handful of canary/pilot tenants (c0224 in the running
example) need the field at all — not all 250. A deployment with no `tenant`
set (a shared/platform deployment) has no wave to look up either; it's
treated the same as "general population" rather than erroring.

`strata promote status` resolves each matched deployment's `spec.tenant` ->
`Tenant.spec.wave` to compute the column shown below. This is the same
"attaches around, nothing here changes" shape ADR-0019 already used for
`version` itself — wave is a fact about the *tenant*, not a new list the
promotion document has to carry.

### Disambiguation: this is not `DeploymentLockingModel`

`DeploymentSpecModel.locking` (`DeploymentLockingModel`, already built) is
pipeline-level **state** locking — preventing two concurrent `deploy run`
invocations from colliding on the same deployment. The `--lock` flag below
is a completely different, unrelated concept — freezing one **version
pin's** value so automated refresh tooling won't bump it. Same English word,
two unrelated mechanisms, exactly the kind of overloaded vocabulary this
doc's own "7 concepts for one idea" critique is about — call it out
explicitly here so nobody conflates `deploy run --force` locking semantics
with this.

### Ship visibility before enforcement

The valuable half of v1 was never the gates — it was answering "where is
1.1.0 right now?" That needs only the ring + wave ordering above, no
enforcement, no new persisted state:

```
$ strata promote status dispatcher
ring  deployment               version   wave  status
dev   dispatcher-dev           1.3.0-rc2 -     unverified
tst   dispatcher-tst           1.2.0     -     current
prd   dispatcher-c0224-prd     1.1.0     1     current
prd   dispatcher-c0062-prd     1.1.0     -     current
prd   dispatcher-c0064-prd     1.0.0     -     current   ← behind
```

That's ~15 lines of schema (`Promotion` + `Tenant.spec.wave`) and a
read-only command.

**Phase 2** adds `strata promote check <deployment>` (exit non-zero if the
prior ring's quorum — `require: any_one|all` — isn't met), still no
mutation.

**Phase 3**, only if real demand appears: digests (`VersionPinModel` has no
digest/sha field today; needs the field before any digest-based gate could
exist at all).

### `--lock`: a flag for version pins, surfaced here while the reasoning is fresh

`strata promote status` can show a ring is deliberately "behind" — but
today, marking that as *deliberate* rather than neglected still means
hand-editing the version document's YAML. The schema to do it right already
exists (`VersionPinModel.status: held` + a required `reason`,
[ADR-0019](../decisions/0019-version-pinning.md)) — there's just no CLI
write surface for `kind: version` pins at all yet (`values`/`validate`/
`version` — the CLI-self-version command — are the only commands built so
far; see `v1-consumer-usage.md`'s v2-current-state notes).

When that write surface is designed, it should expose `--lock` as sugar over
`status: held`, requiring the same `reason` the model already enforces for
free:

```
$ strata version pin charts gatus 1.0.0 --lock "chart version could not be confirmed upstream"
```

equivalent to writing:

```yaml
charts:
  gatus:
    version: 1.0.0
    status: held
    reason: "chart version could not be confirmed upstream"
    reviewed: 2026-10-05 # today, set automatically
```

No new schema — `VersionPinModel.validate_reason_present_when_not_current()`
already rejects a `held` pin with no `reason`, so `--lock` without a reason
string simply fails the same validation a hand-edit would. This fixes the
flag's contract now; the actual `version pin`/`version set` command it
attaches to is a separate, not-yet-designed effort.

### Worked example: all four kinds, end to end

One `workspace` (what runs), one `version` (which pins), one `deployment`
per customer (where + with what values), and one `promotion` (the rollout
order across them) — trimmed to only the fields this example touches.

```yaml
# workspaces/dispatcher.yaml — what runs, and in what order
apiVersion: strata.huybrechts.xyz/v2
kind: workspace
meta:
  name: dispatcher
spec:
  provisioners:
    - name: dispatcher-helm
      tool: helm
      source: { remote: infra, source_path: charts/dispatcher }
  execution:
    - name: deploy-app
      provisioner: dispatcher-helm
      targets: [dispatcher]
```

```yaml
# versions/dispatcher-prd.yaml — which pins this rollout uses
apiVersion: strata.huybrechts.xyz/v2
kind: version
meta:
  name: dispatcher-prd
spec:
  pins:
    charts:
      dispatcher: 1.1.0
```

```yaml
# customers/c0224/tenant.yaml — the sandbox tenant, flagged to bake first
apiVersion: strata.huybrechts.xyz/v2
kind: tenant
meta:
  name: c0224
spec:
  display_name: "Sandbox"
  wave: 1

# customers/c0062/tenant.yaml — an ordinary tenant; no `wave` needed
apiVersion: strata.huybrechts.xyz/v2
kind: tenant
meta:
  name: c0062
spec:
  display_name: "Acme Corp"
```

```yaml
# deployments/dispatcher-c0224-prd.yaml — one per customer, in the prd ring
apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: dispatcher-c0224-prd
spec:
  workspace: dispatcher
  environments: [prd]
  tenant: c0224
  version: dispatcher-prd

# deployments/dispatcher-c0062-prd.yaml — same workspace, same ring, different tenant
apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: dispatcher-c0062-prd
spec:
  workspace: dispatcher
  environments: [prd]
  tenant: c0062
  version: dispatcher-prd
```

```yaml
# promotions/dispatcher.yaml — the rollout order across all of the above
apiVersion: strata.huybrechts.xyz/v2
kind: promotion
meta:
  name: dispatcher
spec:
  workspace: dispatcher
  rings:
    - name: prd
      require: all
```

What reads each document, and when:

| Step                                   | Reads                                                                        | For                                                                                          |
| --------------------------------------- | ----------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `strata build run`                      | `Workspace.spec.execution`/`provisioners`                                    | What to render (the Helm chart invocation itself).                                            |
| `strata deploy run dispatcher-c0062-prd`| `Deployment.spec.version` -> `Version.spec.pins.charts.dispatcher`            | Which chart version this one customer's run actually deploys (the pin overlays the default).  |
| `strata promote status dispatcher`      | `Promotion.spec.workspace`/`rings` to find matching `Deployment`s, each one's `spec.version` -> `Version` for its current pin, each one's `spec.tenant` -> `Tenant.spec.wave` for ordering | The rollout table — no document here is edited to produce it; it's all derived. |

Adding customer #251 means writing one new `deployment` document (and a
`tenant` document, which already has to exist). Nothing under `workspaces/`,
`versions/`, or `promotions/` changes.

## Related Decisions

- [ADR-0019](../decisions/0019-version-pinning.md) — dropped `spec.ring` from
  `kind: version`, and named ring/promotion as the planned follow-up this
  doc delivers; also the source of `VersionPinModel`'s `status`/`reason`
  fields the `--lock` flag builds on.
- [v2-schema-overview.md](../design/v2-schema-overview.md) — `deployment`
  row's "Remaining Work" note ("`gates`/`promotion` remain undesigned") is
  the gap this doc closes; update that doc's table once `kind: promotion`
  is actually implemented.

## Remaining Work / Open Questions

- Zero real `kind: promotion` documents exist anywhere — like `version`'s
  own ring/progression note in ADR-0019, this is forward design, not a
  census-backed port. Revisit the selector shape (`workspace` +
  `environments`/`environment`) against a real second rollout once one
  exists.
- `require: any_one|all` quorum math and `promote check`'s exit-code
  contract aren't detailed yet — left for the Phase 2 design pass.
- Whether a shared/platform deployment with no `tenant` should ever be
  allowed an explicit wave of its own (today: always general population,
  no lookup possible) is unresolved — no real example to test against.
- The `--lock` flag's host command (`version pin`/`version set`) doesn't
  exist yet; only the flag's contract (sugar over `status: held` +
  `reason`) is fixed here.
- Digests (Phase 3) remain deferred — the one v1 concept this doc does
  *not* pull forward, pending a real digest field on `VersionPinModel` and
  actual demand.

## Changelog

- 2026-10-05: Created — cut-down design of v1's `promotion_model.py`
  (ring + order + wave, dropping `strategy`/`type`/`scope`/digests/
  promotion records). Reworked the initial draft's per-deployment
  enumeration into a `workspace`+`environment` selector, and relocated
  `wave` onto `Tenant.spec.wave` (opt-in, unset = general population)
  instead of listing every deployment's wave number by hand. Added the
  `--lock` flag design for a future version-pin write command.
