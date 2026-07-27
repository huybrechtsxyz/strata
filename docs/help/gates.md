# Deployment Gates

Hand-off points where execution pauses for external approval or verification.

Gates are declared under `spec.gates` in the **environment YAML** (not the
configuration). A gate creates a work item when triggered, notifies interested
parties, and resumes deployment once resolved. Gates include approval, cost
review, manual verification, and scheduled deployment.

See ADR-0057 for the full orchestration design.

---

## Gate Types

| Type              | Trigger                        | Resolver             | Example                                   |
| ----------------- | ------------------------------ | -------------------- | ----------------------------------------- |
| `approval`        | Always (or when condition met) | Human operator       | Require ops team sign-off before deploy   |
| `cost_review`     | Cost delta exceeds threshold   | Finance / team lead  | Require approval if monthly cost +$1000   |
| `security_review` | SBOM audit finds critical CVEs | Security team        | Require risk acceptance before deploy     |
| `verify`          | Post-deploy health checks      | Operator             | Manual confirmation service is healthy    |
| `scheduled`       | Deployment window opens        | Clock (auto-resolve) | Deploy only at 2am UTC maintenance window |

---

## Configuration in Environment YAML

```yaml
# environments/production.yaml
spec:
  gates:
    # Human approval required for all production deploys
    - type: approval
      when: always
      approvers: [ops-team, on-call]
      min_approvals: 1
      timeout_minutes: 60

    # Finance approval if cost rises > $1000/month
    - type: cost_review
      when:
        cost_delta_monthly: ">= 1000"
      approvers: [finance]

    # Security review if SBOM finds critical CVEs
    - type: security_review
      when:
        cve_critical: ">= 1"
      approvers: [security-team]

    # Scheduled deployment (auto-resolves)
    - type: scheduled
      when:
        time_utc: "02:00-04:00"    # maintenance window
      auto_resolve: true
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
