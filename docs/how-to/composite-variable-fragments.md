# How To: Compose One Variable's Value From Several Files (Per-Customer/Per-Ring Fragments)

You have one value — e.g. an Azure Application Gateway's `appgateway_config`, a deeply nested
`map(object({ hosts, path_rules, waf_policy, ... }))` Terraform variable — that different
customers, teams, or rings need to contribute their own piece of, from their own file, without
editing one shared blob every time someone onboards. `kind: environment`'s `spec.variables`
(`store: constant`) can't do this: two Environment documents declaring the same variable `key`
have the **second one's value win entirely** (whole-value override, not a combine) — see
[docs/design/composite-variable-merge.md](../design/composite-variable-merge.md) for the full
investigation. This guide covers the pattern that already solves it today, with no new strata
code.

## The short answer

**Author the shared value under `spec.properties`, not `spec.variables`.** Split it across as
many Environment documents as you have contributors (one per customer, one per ring, whatever
your real ownership boundary is) — each document sets only its own fragment, nested at whatever
path it owns. Every `properties` dict a reachable Environment declares is **recursively deep
merged** (Workspace → each reachable Environment, in order → the Deployment's own), not
overridden — confirmed directly against real code
(`merge_workspace_environment_deployment_properties()`,
`strata/controllers/value_controller.py`) and
[experimentally verified](../design/composite-variable-merge.md) by actually running three
fragment documents through it. The merged result is delivered to Terraform exactly like any
other property — written straight into `properties.auto.tfvars.json`, which Terraform
auto-loads with no `-var-file` flag needed.

## Worked example

Three separate files, three separate owners, one combined `appgateway_config`.

**`customers/unisonplanning/environment.yaml`** — owned by the `unisonplanning` team, declares
their own domain and their first ring:

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: unisonplanning-agw
spec:
  properties:
    appgateway_config:
      unisonplanning:
        domain: "unisonplanning.com"
        hosts:
          c0224:
            domain_name: "c0224-agw-dev.westeurope.cloudapp.azure.com"
            backend_pools: ["apim", "dispatcher-dev", "datahub-admin-dev"]
```

**`customers/unisonplanning/ring-c0225.yaml`** — a *later* onboarding, adding a second ring to
the *same* customer, from its own file — this is the real scaling case ("add a new ring to a
customer" shouldn't mean editing someone else's file):

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: unisonplanning-agw-ring2
spec:
  properties:
    appgateway_config:
      unisonplanning:
        hosts:
          c0225:
            domain_name: "c0225-agw-stage.westeurope.cloudapp.azure.com"
            backend_pools: ["apim", "dispatcher-stage"]
```

**`customers/acmecorp/environment.yaml`** — a different customer entirely, owned by a different
team, contributing their own top-level key:

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: environment
meta:
  name: acmecorp-agw
spec:
  properties:
    appgateway_config:
      acmecorp:
        domain: "acmecorp.com"
        hosts:
          c0300:
            domain_name: "c0300-agw-dev.westeurope.cloudapp.azure.com"
            backend_pools: ["apim"]
```

**The deployment lists all three**, in any order that doesn't matter here (none of these
fragments collide on the same leaf key):

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: deployment
meta:
  name: hub-agw
spec:
  workspace: hub-agw-workspace
  environments:
    - unisonplanning-agw
    - unisonplanning-agw-ring2
    - acmecorp-agw
```

**The result**, exactly what `properties.auto.tfvars.json` would contain — and exactly what
`terraform plan` sees as `var.appgateway_config`:

```json
{
  "appgateway_config": {
    "unisonplanning": {
      "domain": "unisonplanning.com",
      "hosts": {
        "c0224": { "domain_name": "c0224-agw-dev.westeurope.cloudapp.azure.com", "backend_pools": ["apim", "dispatcher-dev", "datahub-admin-dev"] },
        "c0225": { "domain_name": "c0225-agw-stage.westeurope.cloudapp.azure.com", "backend_pools": ["apim", "dispatcher-stage"] }
      }
    },
    "acmecorp": {
      "domain": "acmecorp.com",
      "hosts": {
        "c0300": { "domain_name": "c0300-agw-dev.westeurope.cloudapp.azure.com", "backend_pools": ["apim"] }
      }
    }
  }
}
```

Nothing was dropped — both rings under `unisonplanning`, both customers side by side — because
every fragment nests at a *different* path. Onboarding a fourth customer, or a third ring, means
adding one more small file and one more line in `spec.environments` — never touching the other
owners' files.

## Rules that actually matter

- **Merge order is `environments:` list order, later wins per leaf key.** A Tenant's own
  `environments` are already folded in ahead of a Deployment's own list
  (`reachable_environments()`), so the full precedence is: Workspace's own `properties` →
  Tenant's `environments`, in order → Deployment's own `environments`, in order → the
  Deployment's own `properties`. If two fragments genuinely need to set the *exact same* leaf
  key, the one named later always wins — silently, with no collision warning. Keep fragments
  non-overlapping by construction (each customer/ring owns its own subtree) rather than relying
  on ordering to resolve a real conflict.
- **Dicts merge recursively; lists do not.** `deep_merge()`
  (`strata/utils/dict_merge.py`) merges nested `dict` values at any depth, but a list-valued leaf
  (like `backend_pools` above) is **replaced wholesale**, not concatenated or deduped. Never
  have two different fragments both set the same list-valued field expecting them to combine —
  one will silently and completely discard the other's list. Keep list-valued fields inside the
  deepest leaf a single fragment owns (as in the example: each ring owns its own
  `backend_pools`), never at a level two different fragments both touch.
- **Strata stays opaque to what's inside the value (ADR-0025).** Nothing here validates
  `domain_name`/`backend_pools`/`waf_policy` shape — a typo inside a fragment is invisible to
  strata; the real Terraform module's own `variable` type constraint is the only thing that will
  ever catch it, at `terraform plan` time.
- **This is Terraform-specific as confirmed so far** — `properties`/`custom` are delivered via
  `properties.auto.tfvars.json`/`custom.auto.tfvars.json`. Whether the same convention reaches
  Helm/Compose targets the same way `spec.variables` does has not yet been checked; don't assume
  it does for a non-Terraform provisioner without verifying first.
- **You give up `VariableStoreModel`'s typed wrapper** (`type`/`description`, HCL-emission
  hints) by moving a value out of `spec.variables`. If a real variable actually relies on those
  (a declared `type: map`/`type: list` cross-check, or documentation `description`), keep it
  under `spec.variables` instead — this pattern is for values that need **composing from
  several files**, not a wholesale replacement for `spec.variables`.

## Related

- [docs/design/composite-variable-merge.md](../design/composite-variable-merge.md) — the full
  investigation (why `spec.variables` can't do this, what v1 had and didn't generalize, and the
  experiment that confirmed this pattern against real code).
- [docs/design/gap_fit_v1.md](../design/gap_fit_v1.md) gap #19 — the tracked gap this closes.
- [docs/design/build-time-value-categories.md](../design/build-time-value-categories.md) — how
  `spec.properties`/`.custom` are merged and delivered end to end.
- [generate-per-instance-terraform-blocks.md](generate-per-instance-terraform-blocks.md) — once
  you have a composite value like this, generating one static Terraform block per entry (e.g. a
  `provider`/`module` pair per customer) from a template, instead of hand-maintaining one file
  per instance.
