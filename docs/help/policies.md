# Policies

Rules that enforce governance across the deployment lifecycle.

Policies are declared under `spec.policies` in `configuration.yaml` and are
evaluated at four phases: `validate`, `build`, `plan`, and `deploy`. Each policy
can pass, warn, or fail, depending on its enforcement mode.

See ADR-0006 for the full design.

---

## Policy Types

| Type                  | Trigger                                 | Example                                                       |
| --------------------- | --------------------------------------- | ------------------------------------------------------------- |
| `tenant_zone`         | Resource region outside allowed zones   | Deny AWS resources in `us-west-2` if only `us-east-1` allowed |
| `required_tags`       | Missing required tags on resources      | Deny resources without `env` and `owner` tags                 |
| `cost_threshold`      | Infracost delta exceeds budget          | Warn if monthly cost +$500; deny if +$5000                    |
| `ai_review`           | AI plan analysis detects risk ≥ high    | Require approval before apply                                 |
| `policy_agent_result` | Custom OPA policy fails                 | Deny based on custom policy logic                             |
| `max_resource_change` | Plan creates/deletes too many resources | Warn if deletes > 10; deny if > 50                            |

---

## Basic Example

```yaml
spec:
  policies:
    # Enforce tagging
    - name: required_tags
      type: required_tags
      phase: validate
      enforcement: deny
      configuration:
        tags: [env, owner, cost_center]
        except_kinds: []

    # Enforce zones
    - name: prod_zones
      type: tenant_zone
      phase: validate
      enforcement: deny
      configuration:
        allowed_zones: [prod-eu, prod-us]
        denied_zones: []

    # AI-powered plan review
    - name: ai_plan_gate
      type: ai_review
      phase: plan
      enforcement: deny
      configuration:
        risk_threshold: high     # deny if AI says "high" or "critical"
        ai_integration: ai-advisor
```

---

## Enforcement Modes

| Mode   | Behavior                                               |
| ------ | ------------------------------------------------------ |
| `pass` | Policy always passes (useful for testing)              |
| `warn` | Policy fails but command continues (logged as warning) |
| `deny` | Policy failure blocks the command (exit 3)             |

---

## Phases

| Phase      | When                      | Example                                    |
| ---------- | ------------------------- | ------------------------------------------ |
| `validate` | After YAML schema check   | Check for required tags, allowed regions   |
| `build`    | After artifact generation | Check SBOM for CVEs                        |
| `plan`     | After terraform plan      | Check resource count, cost, AI risk        |
| `deploy`   | Before provisioning       | Final checks before infrastructure changes |

---

## Discovery

Run `strata policy check -f deploy.yaml` to evaluate all policies without deploying.

See `strata help --topic audit` for audit trail integration.
