---
name: infrastructure-as-code-best-practices
description: 'Infrastructure-as-Code principles: state management, modularity, versioning, testing, idempotency, and drift detection. Supporting context for the strata-specific skills — use when explaining why a strata pattern exists.'
---

# Infrastructure-as-Code Best Practices

## Core Principles

### 1. Code Is Configuration

**Rule:** Infrastructure is declarative code, not imperative clicks in a UI.

```yaml
# ✅ RIGHT — Infrastructure is version-controlled code
apiVersion: strata.huybrechts.xyz/v1
kind: workspace
meta:
  name: my-platform
spec:
  resources:
    - file: "@config/resources/aks-cluster.yaml"
```

**Benefits:**
- History and audit trail (who changed what, when)
- Code review before deployment (PR reviews catch mistakes)
- Reproducibility (same code = same infrastructure everywhere)
- Disaster recovery (rebuild from code)

---

### 2. State Must Be Tracked & Managed

**Rule:** Infrastructure state lives in a backend, not on your laptop.

**State stores the current reality:**
- What resources exist
- What properties they have
- Relationships between resources
- Current vs desired state

**Why it matters:**
- Without state, IaC can't detect drift (reality diverging from code)
- Without state, destroy operations don't know what to remove
- Shared state = team coordination (prevents concurrent conflicts)

**State backends (pick one):**
- Terraform Cloud (recommended for teams)
- AWS S3 + DynamoDB (for AWS environments)
- Azure Blob Storage (for Azure environments)
- Local file (ONLY for dev/learning, never for production)

**Rule: NEVER commit state files to git.** State contains secrets and is environment-specific.

---

### 3. Modularity & Reusability

**Rule:** Create reusable modules; never copy-paste infrastructure code.

```yaml
# ❌ WRONG — copy/pasted code (maintenance nightmare)
resources:
  - name: aks-cluster-east
    spec: { ... }
  - name: aks-cluster-west
    spec: { ... }  # duplicated; hard to keep in sync

# ✅ RIGHT — reusable module referenced twice with different params
modules:
  - file: "@config/modules/aks-cluster.yaml"
    parameters:
      region: eastus
      name: cluster-east
  - file: "@config/modules/aks-cluster.yaml"
    parameters:
      region: westus
      name: cluster-west
```

**Benefits:**
- Update module once, all uses get the fix
- Consistent infrastructure across projects
- Faster to provision new environments
- Easier testing and validation

**Module structure:**
```
modules/
├── aks-cluster/        # Azure Kubernetes Service
│   ├── main.tf
│   ├── variables.tf
│   └── outputs.tf
├── azure-network/      # VNet, subnets
├── azure-storage/      # Storage accounts
└── security-group/     # Firewall rules
```

---

### 4. Versioning & Promotion

**Rule:** Pin versions. Don't auto-upgrade infrastructure.

```yaml
# ✅ RIGHT — pinned versions
provider:
  version: "3.75.0"  # specific provider version

module:
  file: "@config/modules/aks-cluster.yaml"
  version: "1.2.0"
```

**Promotion flow:**
```
Dev (latest)
  ↓ (tested)
Staging (specific version)
  ↓ (approved)
Production (stable version)
```

**Benefits:**
- Control when infrastructure updates happen
- Test updates in lower environments first
- Rollback if an upgrade breaks things
- Audit trail of version changes

---

### 5. Testing & Validation

**Rule:** Validate infrastructure code before deploying.

**Levels of testing:**

| Level               | Tool                         | When              |
| ---------------------- | -------------------------------- | -------------------- |
| Syntax validation    | `terraform validate`          | Every save         |
| Policy checks         | `checkov`                       | Before build        |
| Cost estimation       | `terraform plan`              | Before deploy       |
| Security scan         | `trivy`                          | Before production   |
| Integration test      | Deploy to test environment    | Before production   |

**In strata:**
```bash
strata validate -f <file> --output json          # Check schema
strata build plan -f deploy.yaml --output json     # Show changes
strata deploy run -f deploy.yaml --dry-run --force --output json     # Dry-run deploy
```

---

### 6. Idempotency

**Rule:** Running the same code twice should produce the same result (no side effects).

```hcl
# ❌ WRONG — not idempotent (creates new resource every time)
resource "azurerm_resource_group" "main" {
  name     = "rg-${timestamp()}"  # new name each run!
  location = "eastus"
}

# ✅ RIGHT — idempotent (same resource every time)
resource "azurerm_resource_group" "main" {
  name     = "rg-myapp-prod"      # static name
  location = "eastus"
}
```

**Why it matters:**
- Deployments are safe to retry (fix and re-apply)
- No accidental duplicate resources
- Workflows (CI/CD) can run repeatedly
- Disaster recovery is automated

---

### 7. Documentation & Naming

**Rule:** Infrastructure code must be self-documenting.

```yaml
# ✅ RIGHT — clear naming and annotations
apiVersion: strata.huybrechts.xyz/v1
kind: resource
meta:
  name: aks-prod-eastus
  annotations:
    description: "Production AKS cluster in East US region"
    purpose: "Runs customer-facing API and services"
    owner: "platform-team@example.com"
  labels:
    environment: production
    criticality: high
    cost-center: platform
```

**Benefits:**
- Other team members understand the purpose
- Audit trail explains why resources exist
- Cost allocation (via labels)
- Compliance documentation

---

### 8. Drift Detection

**Rule:** Infrastructure reality should match code. Detect divergence.

**Drift happens when:**
- Someone manually changes infrastructure in the console
- A resource fails and wasn't recreated
- Network conditions change unexpectedly
- A security patch auto-applies

**Detection:**
```bash
# Show what changed since last deploy
strata build plan -f deploy.yaml --output json

# Check current resource health
strata deploy health -f deploy.yaml --output json
```

**Recovery:**
- Re-run deployment to bring reality back to code
- OR update code to match reality (if change was intentional)

**Best practice:** Regular drift checks (weekly in CI/CD) catch problems early.

---

### 9. Secrets Management

**Rule:** Secrets are NOT infrastructure code.

```hcl
# ❌ WRONG — secret in code
database_password = "super-secret-123"

# ✅ RIGHT — reference secret by name
database_password = var.db_password  # injected at deploy time
```

**Flow:**
1. Store secrets in an integration-backed secret store (Azure Key Vault, HashiCorp Vault, Bitwarden, Infisical)
2. Reference by key name in YAML (never the value itself)
3. At deploy time, strata resolves the actual value
4. Apply infrastructure with real secrets
5. **Secrets never appear in code, logs, or state files**

---

### 10. Scalability & Multi-Environment

**Rule:** Same code, different parameters for different environments.

**Multi-environment pattern:**

```
config/
├── base/                    # Common configuration
│   ├── network.yaml
│   ├── security.yaml
│   └── services.yaml
├── environments/
│   ├── dev.yaml            # Small, cheap resources
│   ├── staging.yaml        # Medium-size, similar to prod
│   └── prod.yaml           # Large, high-availability
```

**Environment differences:**
- Instance size, replica count (resources)
- Networking topology (prod has multi-region)
- Backup retention (prod = long, dev = short)
- Cost management (prod optimized, dev ephemeral)

---

## When to Use IaC

| Scenario                        | Use IaC? | Why                                               |
| ----------------------------------- | ---------- | ------------------------------------------------------ |
| Production infrastructure         | ✅ Yes    | Reproducibility, audit trail, disaster recovery       |
| Development/test environments     | ✅ Yes    | Easy to create/destroy, cost control                  |
| One-time manual setup             | ❌ No     | Overkill for short-lived resources                    |
| Learning/exploration               | ⚠️ Maybe  | Use IaC to document, then clean up                    |

---

## Anti-Patterns to Avoid

| Anti-Pattern              | Problem                                     | Fix                                                  |
| ------------------------------ | ------------------------------------------------ | --------------------------------------------------------- |
| **Console clicking**          | No version control, hard to reproduce       | Use IaC from day 1                                       |
| **Copy-pasted code**          | Maintenance nightmare, inconsistent          | Create reusable modules                                  |
| **Unversioned modules**       | Breaking changes hit all users               | Pin versions, test before upgrade                        |
| **Manual state edits**       | Corrupts consistency between code and state | Use IaC commands only                                    |
| **Secrets in code**          | Security breach, hard to rotate              | Use secret store references                              |
| **No testing**                | Broken deployments in production             | Validate, plan, dry-run first                            |
| **Long drift**                | Reality diverges from code                    | Regular drift checks, rebuild from code                  |
| **No documentation**         | Team doesn't understand infrastructure       | Use annotations, naming conventions, code comments       |

---

## Agent Best Practices

1. **Always validate before deploying** — catch errors before they reach prod
2. **Use dry-run first** — see what will change without committing
3. **Keep code DRY** — use modules for reusability
4. **Version everything** — pin provider versions, module versions
5. **Document with annotations** — why does this resource exist?
6. **Test in dev first** — before staging, before production
7. **Automate drift detection** — regular health checks
8. **Treat state as precious** — backup, encrypt, restrict access
9. **Review before applying** — code review in PR before deploy
10. **Keep secrets secret** — never log or commit actual values
