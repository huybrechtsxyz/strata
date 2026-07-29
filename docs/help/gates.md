# Deployment Gates

Hand-off points where execution pauses for external approval or verification.

Gates are declared under `spec.gates` in the **deployment YAML** (next to
`stages`). A gate creates a work item when triggered, notifies interested
parties, and resumes deployment once resolved. Gates include approval, cost
review, manual verification, and scheduled deployment.

See ADR-0057 for the full orchestration design, and ADR-0059 for the unified
schema (`name`/`mode`/`scope`) that replaced the old, separate `spec.approvals`
metadata block.

---

## Gate Types

| Type              | Trigger                        | Resolver             | Example                                   |
| ----------------- | ------------------------------ | -------------------- | ----------------------------------------- |
| `approval`        | Always (or when condition met) | Human operator       | Require ops team sign-off before deploy   |
| `cost_review`     | Cost delta exceeds threshold   | Finance / team lead  | Require approval if monthly cost +$1000   |
| `security_review` | SBOM audit finds critical CVEs | Security team        | Require risk acceptance before deploy     |
| `verify`          | Post-deploy health checks      | Operator             | Manual confirmation service is healthy    |
| `scheduled`       | Deployment window opens        | Clock (auto-resolve) | Deploy only at 2am UTC maintenance window |

Each gate also has a **mode**:

| Mode      | Behavior                                                                                                                                                                   |
| --------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `enforce` | (default) strata creates a real work item and pauses the deploy (exit code 5)                                                                                              |
| `declare` | strata only records the gate in the audit trail — never blocks. Use when enforcement already happens externally (Azure DevOps approvals, GitHub Actions protection rules). |

And a **scope** — which stage(s) the gate applies to: a list of stage names,
or `"all"` (default).

---

## Configuration in Deployment YAML

```yaml
# deploy.yaml
spec:
  gates:
    # Human approval required before the production stage
    - name: prod-approval
      type: approval
      mode: enforce
      scope: [production]
      when: always
      approvers:
        ops-team:
          type: github-team
          value: "org/ops-team"
        on-call:
          type: user
          value: "oncall@example.com"
      min_approvals: 1
      timeout_minutes: 60

    # Finance approval if cost rises > $1000/month (applies to all stages)
    - name: cost-guard
      type: cost_review
      when:
        cost_delta_monthly: ">= 1000"
      approvers:
        finance:
          type: user
          value: "finance@example.com"

    # Security review if SBOM finds critical CVEs — audit only, external SOC gates it
    - name: sbom-audit
      type: security_review
      mode: declare
      when:
        cve_critical: ">= 1"

    # Scheduled deployment (auto-resolves)
    - name: maintenance-window
      type: scheduled
      when:
        time_utc: "02:00-04:00"    # maintenance window
      auto_resolve: true

  stages:
    - name: staging
    - name: production
```

---

## Workflow

1. **Trigger**: Deployment reaches a gate condition
2. **Create**: Work item generated with unique ID
3. **Notify**: SIEM event sent (Sentinel, Splunk, ELK); alerts fire
4. **Pause**: Exit code 5 (hand-off required); CI picks this up
5. **Resolve**: External actor approves/rejects via `strata workitem approve <id>`
6. **Resume**: `strata deploy run ... --resume <id>` proceeds to apply

---

## CLI Commands

```
strata workitem list [--type TYPE] [--status STATUS]
strata workitem show <id>
strata workitem approve <id> [--note TEXT]
strata workitem reject <id> [--reason TEXT]
strata deploy run -f deploy.yaml --resume <id>
```

---

## Discovery

- `strata help --topic audit` — SIEM notifications and audit trail
