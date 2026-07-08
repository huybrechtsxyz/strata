# Policy Engine Phase 1 — Implementation Decisions

**Author:** Linus
**Date:** 2026-06-15
**ADR reference:** `docs/decisions/0006-policy-engine-for-deployment-guardrails.md`

## Decisions made during implementation

### 1. `PolicyResult.violations` has a default empty list (not required)

The ADR spec shows `violations: List[str]` without a default. Using `field(default_factory=list)` avoids forcing callers of graceful-skip paths to pass an explicit empty list, which is error-prone. All non-trivial results still populate violations explicitly.

### 2. `show_plan()` used instead of `load_plan_json()`

The task spec referenced `load_plan_json()` — that method does not exist on `TerraformDeployer`. The correct method is `show_plan()` which returns `(bool, Dict[str, Any], List[str])` and runs `terraform show -json <stage>.tfplan`. Policy evaluation uses `hasattr(deployer, "show_plan")` so it only runs for Terraform stages.

### 3. `_evaluate_plan_policies` is an inline method on `RunDeployCommand`

The task required wiring at the `deploy_plan_after` gate. Keeping the method on the command (rather than extracting to a controller) follows the project pattern: controllers are orchestrators, commands are thin wrappers. A policy engine is a validator, not a controller.

### 4. `Optional[List[PolicyModel]] = Field(default_factory=list)` on `ConfigurationSpecModel`

Using `default_factory=list` means the field is never `None` — it's an empty list when omitted from YAML. This is consistent with `integrations` field on the same model. The `Optional` wrapper is kept for forward compatibility (allows explicit `null` in YAML to mean "no policies").

### 5. Region normalization strips spaces and lowercases

Terraform plan JSON may report `"West Europe"` while zone configuration uses `"westeurope"`. Both sides are normalized with `.lower().replace(" ", "")` before comparison.
