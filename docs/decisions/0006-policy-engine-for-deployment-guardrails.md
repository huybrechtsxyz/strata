# Policy engine for deployment guardrails

- Status: proposed
- Date: 2026-06-15

## Context and Problem Statement

strata validates configuration at two points today: **validate** (Pydantic schema + Phase 2 cross-references) and **build** (zone alignment, customer constraint checks). Both happen before any infrastructure is provisioned.

The gap is at **deploy time**. When someone edits Terraform/Ansible files directly, adds resources in a disallowed region, or runs `terraform apply` outside strata, the build-time checks have already passed. There is no runtime verification that the actual infrastructure plan respects operational constraints like customer zone restrictions, tagging requirements, or naming conventions.

The customer model (introduced in v1) exposed this clearly: a customer declares allowed zones, and the build phase validates that providers are in those zones — but nothing prevents a `.tf` file from creating a resource in `us-east-1` when the customer only allows `eu-west`. The plan JSON has that information, but strata doesn't inspect it.

Beyond zones, users have expressed interest in enforceable constraints at multiple lifecycle phases:

- **Validate**: naming conventions, required labels, schema extensions
- **Build**: tag completeness, cost estimation thresholds, forbidden resource types
- **Plan**: region/zone enforcement, resource count limits, drift detection
- **Deploy**: deployment manifest completeness, audit trail requirements

A one-off zone check solves the immediate problem but doesn't scale. What's needed is a policy framework — a structured way to declare, evaluate, and enforce constraints across the strata lifecycle.

## Considered Options

- **Option A**: External policy framework (OPA/Conftest, Checkov, Sentinel)
- **Option B**: Strata-native policy engine following the integration pattern
- **Option C**: Hybrid — native engine with external tool delegation

## Decision Outcome

Chosen: **Option C — Hybrid**, because it gives strata a first-class, zero-dependency policy system for common constraints while preserving an escape hatch to industry-standard tools for organisations that already use them.

The native engine handles the 80% case (zone enforcement, required tags, naming patterns) with pure Python policies declared in YAML. The `script` policy type delegates to any external command — OPA, Checkov, custom scripts — for the remaining 20%.

### Consequences

- Good: Zero external dependencies for built-in policies — works out of the box.
- Good: Declarative YAML configuration follows the same pattern as integrations.
- Good: `script` type provides day-one escape hatch to OPA, Checkov, or any custom tool.
- Good: Enforcement levels (`deny`, `warn`, `audit`) give operators graduated control.
- Bad: Built-in policies are limited to what strata ships — custom Python policies require contributing to strata or using the script escape hatch.
- Bad: Policy context varies by phase — policies must be written for a specific phase.
- Bad: Script-type policies have subprocess overhead and error handling complexity.

## Detailed Design

### Architecture

Policies follow the same layered pattern as the rest of strata:

```
models/
  policy_model.py              ← PolicyModel (YAML config), PolicyResult
validators/
  policies/
    base_policy.py             ← BasePolicy ABC
    customer_zone_policy.py    ← Built-in: zone enforcement
    required_tags_policy.py    ← Built-in: tag enforcement
    naming_policy.py           ← Built-in: naming conventions
    script_policy.py           ← Escape hatch: external command
```

Policies are **not** integrations. Integrations wrap external tools with singleton lifecycle and availability checks. Policies are stateless evaluators — instantiated per-evaluation, no singleton, no availability dance. They sit in the `validators/` layer because that's where validation logic lives.

### YAML Declaration

Policies are declared in `configuration.spec.policies`:

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
spec:
  policies:
    # Built-in: customer zone enforcement
    - name: zone_enforcement
      type: customer_zone
      phase: plan
      enforcement: deny
      description: "Ensure all planned resources are in customer-allowed zones"

    # Built-in: required tags on all resources
    - name: required_tags
      type: required_tags
      phase: build
      enforcement: deny
      configuration:
        required:
          - environment
          - owner
          - cost_center

    # Built-in: naming pattern enforcement
    - name: naming_convention
      type: naming_pattern
      phase: validate
      enforcement: warn
      configuration:
        pattern: "^[a-z][a-z0-9_-]+$"
        scope: all  # all | resources | namespaces | modules

    # External: OPA/Conftest check on Terraform plan
    - name: security_baseline
      type: script
      phase: plan
      enforcement: deny
      configuration:
        command: "conftest test --policy policies/ --input"
        input: plan_json  # plan_json | platform_json | deployment_yaml
        timeout: 60
```

### Policy Model

```python
class PolicyModel(PlatformBaseModel):
    name: PlatformName
    type: str                              # customer_zone | required_tags | naming_pattern | script
    phase: str                             # validate | build | plan | deploy
    enforcement: str = "deny"              # deny | warn | audit
    description: Optional[str] = None
    configuration: Optional[Dict[str, Any]] = None
    enabled: bool = True
```

### Base Policy

```python
class BasePolicy(ABC):
    def __init__(self, policy_model: PolicyModel):
        self.policy = policy_model

    @abstractmethod
    def evaluate(self, context: PolicyContext) -> PolicyResult:
        """Evaluate the policy against the given context."""
        raise NotImplementedError

    @property
    def phase(self) -> str:
        return self.policy.phase

    @property
    def enforcement(self) -> str:
        return self.policy.enforcement
```

### Policy Context

Each phase provides different data. The `PolicyContext` is a typed container:

```python
@dataclass
class PolicyContext:
    phase: str                                           # which phase is running
    deployment_service: Optional[DeploymentService]      # always available
    configuration_service: Optional[ConfigurationService] # always available
    platform_artifact: Optional[PlatformArtifactModel]   # build, plan, deploy
    plan_data: Optional[Dict[str, Any]]                  # plan only (terraform show -json)
    manifest: Optional[DeploymentManifestModel]           # deploy only
    build_path: Optional[Path]                           # build, plan, deploy
    work_path: Optional[Path]                            # always available
```

### Policy Result

```python
@dataclass
class PolicyResult:
    passed: bool
    policy_name: str
    enforcement: str             # deny | warn | audit
    violations: List[str]        # human-readable violation messages
    details: Optional[Dict[str, Any]] = None  # structured data for programmatic consumption
```

### Enforcement Levels

| Level   | Behaviour                                                                                        |
| ------- | ------------------------------------------------------------------------------------------------ |
| `deny`  | Policy failure stops the pipeline. Exit code 3 (validation failure).                             |
| `warn`  | Policy failure is logged as a warning. Pipeline continues.                                       |
| `audit` | Policy result is recorded in the deployment manifest only. No console output unless `--verbose`. |

### Policy Evaluation Hook Points

| Phase      | Where in code                                                                  | What triggers it                 |
| ---------- | ------------------------------------------------------------------------------ | -------------------------------- |
| `validate` | `PlatformValidator.validate()` — after service validation passes               | `strata validate -f`             |
| `build`    | `PlatformBuilder.after_build()` or new `_evaluate_policies()` in build command | `strata build run`               |
| `plan`     | `run_deploy_command._execute_stage_provisioning()` — after plan, before apply  | `strata deploy run`              |
| `deploy`   | `base_deploy_command._write_deployment_manifest()` — before persisting         | `strata deploy run` (post-apply) |

The `plan` phase is the most impactful — it sits between `terraform plan` and `terraform apply`, giving policies access to the full plan JSON before any infrastructure change is made.

### Built-in Policy: Customer Zone Enforcement

The motivating use case. Evaluates at `plan` phase:

1. Read `customer.auto.tfvars.json` from the working directory → get allowed zones
2. Read the zone-to-region mapping from `ConfigurationService.model.spec.zones`
3. Build the set of allowed regions from the customer's zones
4. Parse `plan_data["resource_changes"]` — for each resource with `actions: [create]` or `actions: [update]`:
   - Extract `change.after.location` or `change.after.region` (provider-dependent)
   - Check if it's in the allowed regions set
5. Any resource in a disallowed region → violation

```python
class CustomerZonePolicy(BasePolicy):
    def evaluate(self, context: PolicyContext) -> PolicyResult:
        violations = []
        # ... resolve allowed regions from customer zones + config zone mapping
        for change in plan_data.get("resource_changes", []):
            actions = change.get("change", {}).get("actions", [])
            if "create" not in actions and "update" not in actions:
                continue
            after = change.get("change", {}).get("after", {})
            region = after.get("location") or after.get("region")
            if region and region not in allowed_regions:
                violations.append(
                    f"Resource '{change['address']}' targets region '{region}' "
                    f"which is not in customer zones {customer_zones}"
                )
        return PolicyResult(
            passed=len(violations) == 0,
            policy_name=self.policy.name,
            enforcement=self.enforcement,
            violations=violations,
        )
```

### Built-in Policy: Required Tags

Evaluates at `build` phase against the platform artifact:

- Checks every resource in `platform.spec.resources` for required tags/labels
- Checks every namespace in `platform.spec.namespaces`
- Configurable via `configuration.required` list

### Built-in Policy: Naming Pattern

Evaluates at `validate` phase:

- Checks `meta.name` of the validated document against the configured regex pattern
- Scope controls which document kinds are checked

### Script Policy (Escape Hatch)

Evaluates at any phase by running an external command:

1. Prepare the input file (plan JSON, platform JSON, or deployment YAML)
2. Run the configured command with the input file path appended
3. Exit code 0 → pass, non-zero → fail
4. Capture stdout as violation messages

This supports OPA/Conftest, Checkov, tfsec, or any custom script:

```yaml
# OPA via conftest
- name: opa_security
  type: script
  phase: plan
  enforcement: deny
  configuration:
    command: "conftest test --policy policies/ --input"
    input: plan_json

# Checkov
- name: checkov_scan
  type: script
  phase: plan
  enforcement: warn
  configuration:
    command: "checkov -f"
    input: plan_json

# Custom Python script
- name: cost_check
  type: script
  phase: plan
  enforcement: warn
  configuration:
    command: "python scripts/check_costs.py"
    input: plan_json
    timeout: 120
```

### Policy Evaluation Engine

A `PolicyEngine` class coordinates evaluation. It is **not** a service or controller — it's a utility in `validators/policies/`:

```python
class PolicyEngine:
    def __init__(self, policies: List[PolicyModel]):
        self._policies = [self._create(p) for p in policies if p.enabled]

    def evaluate(self, phase: str, context: PolicyContext) -> List[PolicyResult]:
        """Run all policies for the given phase. Returns results."""
        results = []
        for policy in self._policies:
            if policy.phase == phase:
                result = policy.evaluate(context)
                results.append(result)
        return results

    def has_denials(self, results: List[PolicyResult]) -> bool:
        return any(not r.passed and r.enforcement == "deny" for r in results)
```

### CLI Integration

Policies are evaluated automatically during `validate`, `build`, and `deploy`. Additionally:

```
strata validate -f deployment.yaml          # runs 'validate' phase policies
strata build run -f deployment.yaml         # runs 'build' phase policies after build
strata deploy run -f deployment.yaml        # runs 'plan' phase policies between plan/apply
                                            # runs 'deploy' phase policies after apply
```

A future `strata policy` command group could add:

```
strata policy list                          # list configured policies
strata policy check -f file --phase plan    # dry-run policies without full deploy
```

### Deployment Manifest Integration

Policy results are recorded in the deployment manifest:

```yaml
spec:
  policy_results:
    - name: zone_enforcement
      phase: plan
      passed: true
      enforcement: deny
    - name: required_tags
      phase: build
      passed: false
      enforcement: warn
      violations:
        - "Resource 'xyz_vm_worker' missing required tag 'cost_center'"
```

This requires adding `policy_results: Optional[List[PolicyResultModel]]` to `DeploymentManifestSpecModel`.

## Implementation Plan

### Phase 1: Foundation (minimal viable policy engine)

1. `PolicyModel` in `models/policy_model.py`
2. `PolicyContext`, `PolicyResult`, `BasePolicy`, `PolicyEngine` in `validators/policies/`
3. `configuration.spec.policies` field on `ConfigurationSpecModel`
4. Hook `PolicyEngine.evaluate("plan", ...)` into `run_deploy_command.py` between plan and apply
5. `CustomerZonePolicy` — the motivating built-in policy
6. Tests for the engine and zone policy

### Phase 2: Additional built-in policies

7. `RequiredTagsPolicy` (build phase)
8. `NamingPolicy` (validate phase)
9. `ScriptPolicy` (any phase — escape hatch)
10. Hook into validate and build commands

### Phase 3: CLI and audit

11. `strata policy list` / `strata policy check` commands
12. Policy results in deployment manifest
13. Documentation

## Pros and Cons of the Options

### Option A: External policy framework (OPA/Conftest/Checkov)

- Good: Industry standard — large policy ecosystems, well-documented.
- Good: Organisations already using OPA can reuse existing policies.
- Bad: Adds a runtime dependency (OPA binary must be installed).
- Bad: Forces users to learn Rego/HCL policy language — steep learning curve for simple checks.
- Bad: Doesn't integrate with strata's lifecycle phases — would need custom glue code anyway.
- Bad: Can't enforce policies at validate/build phases (these tools only understand Terraform plans or HCL).

### Option B: Strata-native policy engine only

- Good: Zero dependencies, pure Python, fits strata's architecture perfectly.
- Good: Declarative YAML — same configuration pattern users already know.
- Good: Full lifecycle coverage (validate → build → plan → deploy).
- Bad: No reuse of existing policy ecosystems (OPA, Checkov).
- Bad: Custom Python policies require contributing to strata — not extensible by end users.

### Option C: Hybrid (chosen)

- Good: Best of both — native for common cases, script for everything else.
- Good: Zero-dependency default experience; optional external tools for power users.
- Good: `type: script` provides infinite extensibility without modifying strata.
- Bad: Two evaluation paths (native + script) increase testing surface.
- Bad: Script policies have weaker error reporting (stdout parsing vs structured results).

## More Information

- The customer zone model (ADR pending) established zone-to-region mappings in `ConfigurationZoneModel`.
- The `validate_store_security_policy()` function in `store_models.py` is a precursor — it enforces security constraints on variable/secret stores at validate time. The policy engine generalises this pattern.
- Terraform plan JSON format: [terraform.io/docs/internals/json-format](https://developer.hashicorp.com/terraform/internals/json-format) — `resource_changes[].change.after` contains the planned attribute values including `location`/`region`.
- Related: [Integrations reference](../platform/integrations.md), [Exit codes](../platform/exit-codes.md), [Validators](../platform/validators.md)
