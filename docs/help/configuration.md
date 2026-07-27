# Configuration Files

Central governance, integrations, policies, and deployment rules.

The `configuration.yaml` file lives at the workspace level (usually cloned from a
shared repo) and defines what is allowed: what providers, integrations, policies,
and deployment patterns the workspace supports. It is read by every `strata`
command.

---

## Where It Lives

```
strata-workspace/
  .strata/
    solution.json
    cli.yaml
  config/
    configuration.yaml         ← Main governance config
  deploy/
    deploy-prd.yaml           ← References the config, not an overlay of it
  environments.yaml           ← Environment-specific settings (optional)
```

---

## Structure — `spec` Sections

| Section          | Purpose                                                        | Example                                 |
| ---------------- | -------------------------------------------------------------- | --------------------------------------- |
| `integrations[]` | External tools (terraform, ansible, ai_agent, siem, etc.)      | See: `strata help --topic integrations` |
| `policies[]`     | Rules evaluated at validate/build/plan/deploy phases           | See: `strata help --topic policies`     |
| `audit{}`        | Deploy-log structure and SIEM forwarding                       | See: `strata help --topic audit`        |
| `gates[]`        | Work-item hand-off gates (approval, cost review, verification) | See: `strata help --topic gates`        |
| `providers[]`    | Allowed cloud providers and regions                            | `aws`, `azure`, `gcp`                   |
| `zones[]`        | Data residency groups (e.g., `[eu, us]`)                       | Restrict resources to zones             |
| `promotions{}`   | Ring-based version progression (dev → stg → prd)               | Release orchestration                   |
| `paths[]`        | Directory structure validation rules                           | Enforce naming conventions              |
| `security{}`     | Store type restrictions (where secrets can live)               | Deny certain secret stores              |

---

## Common Pattern: Minimal Config

A working configuration needs only:

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: myplatform
spec:
  providers:
    - name: aws
      regions: [us-east-1, eu-west-1]
  integrations:
    - name: terraform
      type: terraform
```

Everything else is optional governance.

---

## Discovery

- `strata schema get configuration` — Full JSON schema (raw, for machines)
- `strata schema explain configuration` — Human-readable field listing
- `strata new configuration` — Scaffold a template
- `strata help --topic integrations` — What integrations can do
- `strata help --topic policies` — How policy rules work
- `strata help --topic gates` — Approval and hand-off gates
