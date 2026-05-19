# Platform Architecture

Modular, multi-repository infrastructure platform for managing VM workspaces with cluster orchestration. Separation-of-concerns design enables audit-proof deployments (NIS2, ISAE 3402 Type 2 compliant).

## Core Principles

- **Separation of Concerns:** Platform code | Modules | Configuration | Deployments (separate repos)
- **Version Control Everything:** Git commits = immutable audit trail
- **Declarative Configuration:** YAML-defined infrastructure, reproducible deployments
- **Audit Trail:** Complete deployment manifests with version references

## Multi-Repository Structure

**strata** - Core platform (CLI, provisioners, defaults)

- `src/STRATA_platform/` - Python source
- `config/` - Default configurations, providers, resources, firewalls
- `deploy/` - IaC templates (Terraform, Ansible)
- Versioning: Semantic (v1.0.0, v1.1.0)

**xyz*modules*{name}** - Reusable infrastructure modules (databases, monitoring, networking)

- `modules/` - Module YAML definitions
- `scripts/` - Installation scripts
- Independent versioning per module repo

**xyz*config*{org}** - Organization-specific config

- `configurations/` - Merged org configs
- `providers/` - Provider credentials/settings
- `resources/` - Standard resource definitions
- `firewalls/` - Org firewall rules
- `namespaces/` - Application namespaces
- Versioned for compliance, environment branches (dev/staging/prod)

**xyz*deploy*{env}** - Environment-specific deployments

- `workspaces/` - Workspace definitions
- `environments/` - Environment customizations
- `deployments/` - Deployment manifests
- One repo per environment, deployment tags mark releases

## Configuration Hierarchy

Load order (later overrides earlier):

```
1. Platform Defaults (STRATA_platform/src/STRATA_platform/data/configuration.yaml)
2. Platform Config (STRATA_platform/config/configurations/*.yaml)
3. Organization Config (STRATA_config_{org}/configurations/*.yaml)
4. Environment Config (--config flag)
5. Deployment Overrides (deployment.yaml spec.overrides)
```

**Numeric prefixes control order:** `00-defaults.yaml` → `10-providers.yaml` → `20-topologies.yaml` → `30-validation.yaml` → `99-overrides.yaml`

**Resource reference chain:** Deployment → Environment → Workspace → Topology → Component → Resource → Provider

## Deployment Workflow

**1. Define Infrastructure (workspace.yaml):**

```yaml
apiVersion: platform.huybrechts.xyz/v1
kind: workspace
spec:
  providers:
    - name: kamatera_eu
      file: config/providers/kamatera-eu.yaml
  topology:
    - name: swarm
      provider: kamatera_eu
      components:
        - name: manager
          file: config/resources/vm_manager.yaml
          count: 1
```

**2. Customize for Environment (environment.yaml):**

```yaml
apiVersion: platform.huybrechts.xyz/v1
kind: environment
spec:
  workspace: workspaces/platform.yaml
  overrides:
    topology:
      swarm:
        components:
          manager:
            count: 3 # Production uses 3
```

**3. Create Deployment Instance (deployment.yaml):**

```yaml
apiVersion: platform.huybrechts.xyz/v1
kind: deployment
meta:
  name: platform_prod_2024_q1
  labels:
    environment: production
    audit_ticket: "CHG-2024-001"
spec:
  environment: environments/production.yaml
  deployment:
    auto_approve: false
```

**4. Execute:**

```bash
xyz deploy run -f ../xyz-deploy-prod/deployments/active/platform-current.yaml
```

**5. Generated Audit Manifest (build/manifest.yaml):**
Captures deployment/environment/workspace/configs/resources/providers with Git commits, tags, timestamps, user, infrastructure state.

## Compliance & Audit

**NIS2:** Version-controlled configs, approval workflows, audit trail, change control, secret management, firewall rules, rollback capability

**ISAE 3402 Type 2:** Separation of duties, authorization controls, automated validation, audit logging, deployment manifests, state tracking

**Audit Evidence:** Deployment manifests + Git history + Config files + Approval records (PRs) + Validation reports

## Version Control Strategy

**Tagging:**

- Platform: `v1.0.0` (semantic)
- Config: `v1.0.0 -m "Approved: CHG-2024-001"` (with approval metadata)
- Deploy: `v2024.03.15 -m "Platform v1.2.0, App v2.1.0"` (date-based)

**Branch Strategy:**

- Platform: `main` (stable), `develop`, `feature/*`, `hotfix/*`, `release/*`
- Config: `main` (prod-approved), `staging`, `develop`, `feature/*`
- Deploy: `main` (current), `planned/*`, `rollback/*`

**Commit Messages:** Use conventional commits with audit references:

```
deploy: platform v1.2.0 to production

- Change ticket: CHG-2024-001
- Approved by: ops-lead@acme.com
- Rollback plan: deployments/rollback/platform-v1.1.9.yaml
```

## Best Practices

**Repository Management:**

- One repo per purpose
- Semantic versioning
- Tag every deployment with change ticket
- Maintain rollback branches

**Configuration Management:**

- Layer configs logically with numeric prefixes
- Separate by concern (providers/, resources/, firewalls/, namespaces/)
- Meaningful names (`kamatera-eu-prod.yaml` not `provider1.yaml`)
- Document with annotations/labels

**Deployment Management:**

- Generate manifest for every deployment
- Separate environments (one deploy repo per env)
- Validate before deploy: `validate → plan → apply`
- Commit manifest to Git with change ticket reference

## Troubleshooting

**Config not found:** Check relative path to correct repo
**Merge issues:** Verify config load order (platform → platform config → org → env → overrides)
**Version mismatch:** Check platform version vs config requirements
**Missing credentials:** Verify provider file exists, configure credentials in Bitwarden/Vault or env vars

## Summary

Multi-repository architecture provides:

- ✅ Separation of concerns (platform/modules/config/deploy)
- ✅ Independent versioning with Git audit trail
- ✅ Compliance support (deployment manifests = audit evidence)
- ✅ Collaboration (teams in separate repos)
- ✅ Rollback capability (configs preserved in Git)

**Deployment manifest captures:** Git commits, version tags, timestamp, user, complete config, infrastructure state → **audit-proof evidence** for regulatory compliance.