# Unified Provisioner Integration Contract (Version/Auth/Tool-Validation)

- Status: implemented
- Date: 2026-09-14
- Target release: 2.0.0 — ships alongside
  [ADR-0079](./0079-terraform-integration-resolution-fallback-by-type.md) as
  part of the same breaking-change release.

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

**Confirmed Option D**, refined after auditing every deployer's actual code
(not just the four already known — this closes the "inventory every
deployer" item from the original Remaining Work):

| Deployer                               | Provisioner-scoped? (`_iac_model`) | Today's bug                                                                                                                                               | Fix                                                                      |
| -------------------------------------- | ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `TerraformDeployer`                    | Yes                                | none — already fixed by ADR-0079                                                                                                                          | n/a                                                                      |
| `AnsibleDeployer`                      | Yes                                | hardcoded inline `IntegrationModel(name="ansible", type="ansible")`                                                                                       | wire into `resolve_for_provisioner()`, same as Terraform                 |
| `BicepDeployer`                        | Yes                                | hardcoded inline `IntegrationModel(name="azure", type="azure_cli")` — note: the compatible class is `AzureCLIIntegration`, there is no `BicepIntegration` | wire into `resolve_for_provisioner()`, matching on `AzureCLIIntegration` |
| `ComposeDeployer`                      | **No**                             | hardcoded inline `IntegrationModel(name="docker", type="docker")`                                                                                         | see below — different mechanism                                          |
| `HelmDeployer`                         | **No**                             | hardcoded inline `IntegrationModel(name="helm", type="helm")`                                                                                             | see below — different mechanism                                          |
| `ScriptDeployer`                       | Yes (has `_iac_model`)             | none — `validate_environment()` always returns `True, []`, no external binary                                                                             | no change needed                                                         |
| Sync (`ArgocdDeployer`/`FluxDeployer`) | Yes                                | raw `shutil.which("git")`, doesn't use `IntegrationModel`/`IntegrationService` at all                                                                     | out of scope — different, smaller pattern; not addressed by this ADR     |

**Structural finding not visible from the original audit:** `ComposeDeployer`
and `HelmDeployer` are **not** provisioner-scoped — they operate over
namespace/module services (a stage can deploy many namespaces/charts), never
call `_resolve_iac_model()`, and have no `WorkspaceIacModel` to carry an
explicit `integration:` override. `resolve_for_provisioner()`
(ADR-0079) requires an `iac_model` for exactly that override path, so it
cannot be reused as-is for these two.

**Resolution:** add a second, simpler helper for the non-provisioner-scoped
case — `IntegrationService.resolve_by_class(integration_class,
default_factory=None)`. No explicit-name override (there is no addressable
provisioner entry to carry one), no `iac_model` argument:

1. Auto-bind to the sole registered integration that `isinstance()` of
   `integration_class`.
2. Zero candidates → `default_factory()` if provided (preserves today's
   hardcoded default for Docker/Helm), else raises.
3. More than one candidate → raises (ambiguous, never guesses) — an
   operator with two `type: docker` integrations for Compose has no way to
   pick one today; that's a real limitation worth documenting, not silently
   picking the first.

This keeps `resolve_for_provisioner()` (ADR-0079) unchanged and adds one new,
narrowly-scoped method rather than stretching its contract to cover an
entity (namespace/module) it was never designed to represent.

### Consequences

- Good: closes the Ansible/Bicep/Compose/Helm silent-no-op bug — declaring a
  compatible integration now actually gets used, for all four.
- Good: `AnsibleDeployer`/`BicepDeployer` reuse `resolve_for_provisioner()`
  unchanged — no new mechanism for the provisioner-scoped case.
- Good: `resolve_by_class()` is small, and gives Compose/Helm the same
  "declaring one integration works, declaring two is an ambiguity error"
  behavior without inventing a fake provisioner-scoping concept for them.
- Good: `default_factory` (added to `resolve_for_provisioner()` — see
  ADR-0079's implementation) preserves 100% backward compatibility for the
  overwhelming majority of existing workspaces that never declared an
  ansible/bicep/docker/helm integration at all.
- Bad: two related-but-distinct resolution helpers (`resolve_for_provisioner`
  vs `resolve_by_class`) instead of one, because the underlying entities
  genuinely differ (provisioner-scoped vs capability-scoped) — must be
  documented clearly so plugin authors pick the right one.
- Bad: Compose/Helm integrations can never be explicitly pinned by name (no
  `integration:` field target exists) — only auto-bind applies. Acceptable
  today (matches current single-Docker/single-Helm-CLI reality) but a real
  ceiling if a future workspace legitimately needs two Docker daemons or two
  Helm binaries in one deployment.
- Bad: still doesn't touch sync deployers (ArgoCD/Flux) — their raw
  `shutil.which("git")` check remains unchanged, tracked as explicitly out of
  scope rather than silently forgotten.

## Implementation Notes

Shipped 2026-09-14, in full:

- `IntegrationService.resolve_by_class(integration_class, default_factory=None)`
  added alongside `resolve_for_provisioner()`
  ([src/strata/services/integration_service.py](../../src/strata/services/integration_service.py)).
- `IntegrationResolutionError` generalized to a `subject`/`reason` pair so it
  reads sensibly for both the provisioner-scoped and class-scoped resolution
  paths ([src/strata/exceptions/integration_exception.py](../../src/strata/exceptions/integration_exception.py)).
- `AnsibleDeployer`/`BicepDeployer` rewired into `resolve_for_provisioner()`
  (with `default_factory` preserving each's pre-ADR-0080 hardcoded default);
  `ComposeDeployer`/`HelmDeployer` rewired into `resolve_by_class()` (same
  `default_factory` preservation). Each deployer's `validate_environment()`
  is now a thin wrapper delegating to a private `_get_<x>_integration()`
  method, mirroring `TerraformDeployer`'s own ADR-0079 shape
  ([src/strata/deployers/ansible_deployer.py](../../src/strata/deployers/ansible_deployer.py),
  [bicep_deployer.py](../../src/strata/deployers/bicep_deployer.py),
  [compose_deployer.py](../../src/strata/deployers/compose_deployer.py),
  [helm_deployer.py](../../src/strata/deployers/helm_deployer.py)).
- `WorkspaceIacModel`'s `integration` guard validator relaxed to
  `{terraform, ansible, bicep}` — **not** `compose`/`helm` (no addressable
  field target — see the structural finding above) or
  `script`/`argocd`/`flux` (no integration lookup at all)
  ([src/strata/models/workspace_model.py](../../src/strata/models/workspace_model.py)).
- `WorkspaceService._validate_terraform_integration_bindings()` (ADR-0079)
  renamed to `_validate_provisioner_integration_bindings()` and generalized
  via a small per-type rule table (`{provisioner_type: (integration_class,
  zero_candidates_is_error)}`), so `strata validate --deep` and
  deploy-time resolution agree for terraform/ansible/bicep — zero
  registered is an error only for terraform, matching each deployer's
  `default_factory` argument exactly
  ([src/strata/services/workspace_service.py](../../src/strata/services/workspace_service.py)).
- Docs: `docs/platform/integrations.md` (rewrote the binding section to cover
  both resolution paths and the full deployer table) and
  `docs/config/workspace.md` (extended "Integration Binding" to
  ansible/bicep, with a compose/helm cross-reference).
- Schema: `.strata/schemas/workspace.json` / `platform.json`'s `integration`
  field description updated to match (surgical edit, not a full
  regeneration — see ADR-0079's note on pre-existing unrelated schema drift).
- CHANGELOG (breaking-change) entry under `[Unreleased]`, additive to
  ADR-0079's.
- Tests: unit tests for `resolve_by_class()` and `resolve_for_provisioner()`'s
  `default_factory` parameter
  ([tests/strata/services/test_services_integration_resolve_for_provisioner.py](../../tests/strata/services/test_services_integration_resolve_for_provisioner.py)),
  per-deployer tests for all four rewired deployers exercising the
  default-factory fallback, real auto-bind, and (where applicable)
  explicit-name paths
  ([tests/strata/deployers/test_deployers_ansible.py](../../tests/strata/deployers/test_deployers_ansible.py),
  [test_bicep_deployer.py](../../tests/strata/deployers/test_bicep_deployer.py),
  [test_deployers_compose.py](../../tests/strata/deployers/test_deployers_compose.py),
  [test_deployers_helm.py](../../tests/strata/deployers/test_deployers_helm.py)),
  updated `WorkspaceIacModel` validator tests reflecting the relaxed guard
  ([tests/strata/models/test_models_workspace.py](../../tests/strata/models/test_models_workspace.py)),
  and new deep-validation tests for ansible/bicep
  ([tests/strata/services/test_services_workspace.py](../../tests/strata/services/test_services_workspace.py)).
  Full suite green: 6594 passed, 16 skipped, 0 failed. `mypy .` clean on
  every touched file (pre-existing, unrelated failures elsewhere untouched).

Sync deployers (`ArgocdDeployer`/`FluxDeployer`) remain out of scope, as
decided — their raw `shutil.which("git")` check is unchanged. If a future
need arises to bring them into this contract, that's a new ADR, not a
reopening of this one.

