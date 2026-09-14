# Unified Provisioner Integration Contract (Version/Auth/Tool-Validation)

- Status: proposed
- Date: 2026-09-14
- Target release: 2.0.0 — **release-coupled with
  [ADR-0079](./0079-terraform-integration-resolution-fallback-by-type.md):
  both must land in the same major release.** ADR-0079 adds the generic
  `WorkspaceIacModel.integration` field and the shared
  `IntegrationService.resolve_for_provisioner()` helper but only wires
  Terraform into it; this ADR wires up the remaining provisioner types. If
  this ADR slipped, `integration:` would be silently ignored on non-Terraform
  provisioners — the exact anti-pattern documented below.

## Context and Problem Statement

A reasonable mental model for a DevOps engineer configuring strata is:

1. Declare a provisioner's tool config once in `configuration.yaml`
   (version constraints, auth to reach its backend).
2. Link a `workspace.yaml` provisioner to it by `name` + declare its `type`.
3. Point a `deployment.yaml` stage at the workspace provisioner's `name`.
4. Done — every provisioner type behaves the same way.

Step 4 does not hold today. Auditing every built-in deployer's
`validate_environment()` shows **three different, unrelated mechanisms**
depending on provisioner type:

### 1. Terraform — the model above, for real

`TerraformDeployer._get_terraform_integration()`
([src/strata/deployers/terraform_deployer.py](../../src/strata/deployers/terraform_deployer.py#L856-L871))
does look up `configuration.spec.integrations[]` via `IntegrationService`,
matched by the workspace provisioner's `name`, and uses the result's
`authentication`, `endpoints`, and `validation.{min,max}_version`. This is the
mechanism ADR-0079 covers a specific gap in (no fallback when the name isn't
declared as an integration).

### 2. Ansible — declared config is silently ignored (a bug, not a trade-off)

`AnsibleDeployer.validate_environment()`
([src/strata/deployers/ansible_deployer.py](../../src/strata/deployers/ansible_deployer.py#L169-L186)):

```python
def validate_environment(self):
    # Create a minimal IntegrationModel for the ansible integration
    config = IntegrationModel(name="ansible", type="ansible")
    ansible = AnsibleIntegration(config=config)
```

This builds a **hardcoded, throwaway `IntegrationModel` inline** — it never
calls `IntegrationService.get_integration(...)` and never reads
`configuration.spec.integrations[]` at all. An operator who declares:

```yaml
spec:
  integrations:
    - name: config_mgmt
      type: ansible
      validation: { min_version: "2.15" }
```

gets no error, no warning, and no effect — the declaration is dead YAML.
Ansible's *actual* auth (SSH private key) comes from a wholly different path:
`workspace.spec.provisioners[].configuration.ssh_private_key_secret` resolved
through `resolved_values.secrets` (the ADR-0075 shared secret-expression
system), unrelated to `IntegrationService` entirely.

### 3. Helm / Compose — a third, different mechanism

Any provisioner-level secret (registry creds, tokens) is expressed inline via
`${secret:KEY}` / `${var:KEY}` / `${feature:KEY}` (ADR-0075) directly in the
values/env content, resolved from `resolved_values` — again bypassing
`IntegrationService` and `configuration.spec.integrations` entirely.

### 4. Script / ArgoCD / Flux / Bicep — no surface at all

No dedicated version/auth declaration mechanism; fully ambient (`PATH`,
`kubeconfig`, shell environment).

### Why this matters

- **Silent no-op is worse than an error.** The Ansible case passes schema
  validation, looks like it should do something, and does nothing — an
  operator has to read deployer source to discover this, exactly how this gap
  was found.
- **Undocumented inconsistency erodes trust in the schema.** `type: ansible`
  and `type: terraform` integrations look identical in `configuration.yaml`
  but mean "wire this in" for one and "ignored" for the other.
- **New provisioner plugins have no contract to follow.** Without a single
  documented pattern, each new built-in or user plugin
  ([docs/platform/provisioner-plugin-api.md](../platform/provisioner-plugin-api.md))
  invents its own auth/version story, compounding the inconsistency.

This is broader than ADR-0079 (which only addresses Terraform's name-match
resolution gap) — it's about whether *any* provisioner type reliably supports
the config → workspace → deploy chain a user would reasonably assume applies
uniformly.

## Considered Options

- **A. Status quo, documented.** Leave each deployer's mechanism as-is;
  clearly document per-type behavior so operators don't assume uniformity.
  Cheapest, but leaves the Ansible dead-config bug in place and gives plugin
  authors no contract to follow.
- **B. Unify everything onto the Terraform pattern.** Every deployer's
  `validate_environment()` resolves its tool config through
  `IntegrationService`, matched by workspace provisioner name (with the
  ADR-0079 type-fallback). Ansible's hardcoded inline `IntegrationModel` is
  replaced with a real registry lookup; Helm/Compose gain the same option for
  registry/tool-level auth (distinct from the values-level `${secret:}`
  substitution they'd keep for application config). One mechanism, one mental
  model, matches what engineers already assume.
- **C. Unify everything onto the lighter secret-expression pattern
  (ADR-0075).** Drop `IntegrationService`/named integrations for
  provisioner-level auth entirely; every provisioner's auth is a
  `${secret:KEY}` reference resolved from `resolved_values`. Tool-availability
  / version-range checks become a separate, uniform "requirements" concern
  that doesn't need `configuration.yaml` at all (e.g. probed directly from the
  binary on `PATH`, or declared directly on the workspace provisioner).
  Simpler mental model, but a real regression for Terraform users who rely on
  `endpoints`/structured `authentication` (e.g. Terraform Cloud API address +
  token) that doesn't fit naturally into a single secret string.
- **D. Two-tier split.** Separate the two concerns that are currently
  conflated under "integration":
  - **Tool validation** (is the binary present, is its version acceptable) —
    inherently local/environment, doesn't need `configuration.yaml` at all.
    Every deployer exposes this the same way (a shared
    `BaseIntegration.ensure_available()`-style check), regardless of whether a
    named config-level integration exists.
  - **Backend auth** (credentials/endpoints to reach a remote system:
    Terraform Cloud, an SSH bastion, a Helm OCI registry) — stays
    config-owned, resolved through one shared
    `IntegrationService.get_for_provisioner(workspace_provisioner)` helper
    every deployer calls identically instead of each hand-rolling its own
    lookup (or, in Ansible's case, not looking anything up).

## Decision Outcome

Leaning toward **Option D**, because it keeps the parts that already work
well separate (tool-presence checks genuinely are local/environmental and
Terraform's `endpoints`/`authentication` structure is worth preserving for
config-driven backends) while fixing the actual bug (Ansible's dead-config
lookup) and giving every current and future provisioner type one documented
resolution path instead of three undocumented ones. Full option is not yet
finalized — see Remaining Work.

### Consequences (anticipated)

- Good: closes the Ansible silent-no-op bug — declaring a `type: ansible`
  integration would actually do something, or the schema would reject it if
  genuinely unsupported.
- Good: plugin authors get one documented contract
  (`docs/platform/provisioner-plugin-api.md`) instead of reverse-engineering
  behavior from `terraform_deployer.py` vs `ansible_deployer.py`.
- Good: reuses ADR-0079's resolution algorithm (explicit `integration:` →
  exact lookup; else auto-bind to the sole registered integration of the
  matching class) via its shared
  `IntegrationService.resolve_for_provisioner()` helper, rather than
  inventing a second one.
- Bad: touches every built-in deployer (`ansible`, `helm`, `compose`, `script`,
  sync deployers) — larger surface area than ADR-0079's Terraform-only fix,
  with more room for behavior-change regressions in each.
- Bad: requires a compatibility story for existing workspaces that already
  rely on Ansible's `ssh_private_key_secret` + `resolved_values.secrets` path
  — that mechanism works today and must not silently change behavior for
  existing deployments.

## Remaining Work

<!-- Required while Status is proposed / in-progress / partially-implemented.
     Remove this section once Status becomes implemented. -->

- Not started — nothing in this ADR has been implemented yet.
- Decide definitively between Options B/C/D (this draft leans D but hasn't
  confirmed it against every deployer's existing test suite expectations).
- Inventory every built-in deployer's current `validate_environment()` to
  confirm the B/C/D categorization above is complete (Bicep, sync deployers
  not yet examined in as much depth as Terraform/Ansible/Helm/Compose).
- Design the shared resolution helper's exact signature and where it lives
  (likely `IntegrationService` or a new `deployers/base_deployer.py` method).
  — **resolved by ADR-0079**: it is
  `IntegrationService.resolve_for_provisioner(iac_model, integration_class)`,
  matching on integration *class* (so subclasses like `OpenTofuIntegration`
  bind correctly). This ADR consumes that helper; it does not redesign it.
- Decide whether Ansible's `ssh_private_key_secret` mechanism is kept as-is
  (secret-level, ADR-0075 family) and only the *tool-validation* half moves to
  the shared helper, or whether auth also migrates — needs a compatibility
  plan either way so existing workspaces don't silently change behavior.
- Write migration guidance for any workspace/configuration currently relying
  on today's per-type quirks (e.g. an Ansible integration entry that was
  already silently ignored — confirm no one is relying on that no-op).
- **Wire every non-Terraform deployer into
  `resolve_for_provisioner()`** so the generic `integration:` field added by
  ADR-0079 is actually consumed by each provisioner type — this is the work
  that makes the 2.0.0 release coherent (see the release coupling in the
  header). Per type, decide whether "no integration registered at all" is an
  error or a documented no-op fallback (Compose/Script/sync deployers may
  legitimately have nothing to bind to).
- Coordinate with ADR-0079: both ship in 2.0.0 as a hard break, no
  deprecation window. Neither should ship without the other — see the guard
  condition in ADR-0079's "Release and scope decisions".
