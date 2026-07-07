# OMP Integrator VCT Design

This document outlines the repository structure, configuration, and deployment architecture for **OMP Integrator VCT** (Value Creation Team) as the platform orchestration engine.

## Overview

An integrator deployment manages multi-tenant SaaS infrastructure across hybrid cloud environments. The design uses a **centralized configuration repository** paired with **specialized deployment repositories** for each tool (Terraform, Ansible, Helm/ArgoCD, Logging).

| Repository                    | Purpose                    | Contents                                                                       |
| ----------------------------- | -------------------------- | ------------------------------------------------------------------------------ |
| **iac-int-deploy** (config)   | Central orchestration      | strata configs, deployment manifests, policies                                 |
| **tf-int-main** (deploy)      | Main infrastructure code   | Terraform root modules, networking, compute                                    |
| **tfmod-int-shared** (deploy) | Reusable Terraform modules | Terraform modules library                                                      |
| **ans-int-config** (deploy)   | Server configuration       | Ansible playbooks and roles                                                    |
| **chart-int-deploy** (deploy) | Kubernetes workloads       | Helm charts, ArgoCD apps, platform services                                    |
| **log-int-deploy** (deploy)   | Audit & deployment logging | Logging infrastructure, deployment manifests, audit trails, compliance records |

---

## Quick Start: Creating `iac-int-deploy` & `log-int-deploy`

### Phase 1: Create `iac-int-deploy` Repository (Central Configuration)

**Minimum viable structure to bootstrap:**

```bash
# 1. Create and initialize repository
mkdir iac-int-deploy
cd iac-int-deploy
git init
git config user.email "deployer@omp.com"
git config user.name "OMP Deployer"

# 2. Create minimal folder structure
mkdir -p .strata config/{policies,security,templates,providers} landscape/{bootstrap,environments,workspaces} zones customers/config workspaces scripts

# 3. Create .strata/cli.yaml (workspace defaults)
cat > .strata/cli.yaml << 'EOF'
organization: omp-integrator
project: integrator-platform
output: json
verbose: false
EOF

# 4. Create initial landscape bootstrap manifest
cat > landscape/bootstrap/bootstrap-deploy.yaml << 'EOF'
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: landscape-bootstrap
  annotations:
    description: "Landscape foundation - Global DNS, Secrets, Monitoring"
    tier: landscape
    run_frequency: "once"

spec:
  configuration:
    workspace: landscape-workspace
    environment: landscape
  
  stages:
    - name: landscape-setup
      provisioner: terraform-main
      scope: global
      on_failure: stop
      tasks:
        - name: placeholder
          description: "Will reference tf-int-main when available"
EOF

# 5. Create workspace definition
cat > workspaces/landscape-workspace.yaml << 'EOF'
apiVersion: strata.huybrechts.xyz/v1
kind: workspace
meta:
  name: landscape-workspace
  annotations:
    description: "Landscape - Global orchestration"

spec:
  provisioners:
    - name: terraform-main
      provisioner: terraform
      backend:
        type: terraform_cloud
        configuration:
          organization: omp-integrator
          workspace: landscape-prod
EOF

# 6. Create initial policy
cat > config/policies/global-policies.yaml << 'EOF'
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: global-policies
  annotations:
    description: "Global compliance and governance policies"

spec:
  policies:
    - name: require-tags
      description: "All resources must have owner tag"
    - name: require-encryption
      description: "All storage must be encrypted at rest"
    - name: require-audit-logging
      description: "All deployments must log to log-int-deploy"
EOF

# 7. Create README
cat > README.md << 'EOF'
# iac-int-deploy

Central configuration and orchestration repository for OMP Integrator.

## Quick Start

```bash
strata sln init
strata validate landscape/bootstrap/bootstrap-deploy.yaml --deep
```

## Structure

- `landscape/` - Level 1: Global foundation bootstrap
- `zones/` - Level 2: Regional infrastructure
- `customers/` - Levels 3-4: Tenant & environment deployments
- `config/` - Shared policies, security, providers
- `workspaces/` - Strata workspace definitions
EOF

# 8. Commit initial structure
git add .
git commit -m "Initial iac-int-deploy repository structure"

# 9. Initialize strata workspace
strata sln init
```

---

### Phase 2: Create `log-int-deploy` Repository (Audit Sink)

**Minimum viable structure for audit logging:**

```bash
# 1. Create and initialize repository
mkdir ../log-int-deploy
cd ../log-int-deploy
git init
git config user.email "deployer@omp.com"
git config user.name "OMP Deployer"

# 2. Create folder structure for audit sink
mkdir -p archive/{landscape,zones,customers,manifests,audit-records} \
         policies scripts dashboards templates \
         terraform/{main,backends} helm ansible

# 3. Create archive README
cat > archive/README.md << 'EOF'
# Deployment Manifest & Audit Archive

This directory contains versioned copies of all deployment manifests and audit logs.

## Structure

- `landscape/bootstrap-deploys/` - All landscape bootstrap manifests
- `zones/zone-001/deploy-history/` - Per-zone deployment history
- `customers/{id}/bootstrap-history/` - Customer bootstrap records
- `customers/{id}/prod-deploys/` - Production deployment manifests
- `manifests/` - Versioned deployment YAML files
- `audit-records/` - Compliance & audit trail JSON records
EOF

# 4. Create manifest ingestion policy
cat > policies/manifest-ingestion.yaml << 'EOF'
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: manifest-ingestion
  annotations:
    description: "Rules for processing and storing deployment manifests"

spec:
  ingestion:
    - source: "strata"
      event: "deployment_complete"
      action: "archive_manifest"
      destination: "archive/manifests"
    
    - source: "strata"
      event: "deployment_complete"
      action: "index_in_elasticsearch"
      index: "deployments"
EOF

# 5. Create minimal Terraform for logging infrastructure
cat > terraform/main/main.tf << 'EOF'
# Minimal terraform for logging infrastructure
# To be expanded with elasticsearch, opensearch, and audit trail setup

terraform {
  required_version = ">= 1.0"
  
  backend "s3" {
    bucket = "omp-integrator-tf-state"
    key    = "log-int-deploy/terraform.tfstate"
    region = "us-east-1"
  }
}

provider "aws" {
  region = var.aws_region
}

# Placeholder for logging infrastructure resources
EOF

# 6. Create manifest template
cat > templates/manifest-template.yaml << 'EOF'
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: "{deployment_name}"
  annotations:
    description: "{deployment_description}"
    customer_id: "{customer_id}"
    environment: "{environment}"
    deployment_date: "{iso_timestamp}"
    deployed_by: "{user_email}"

spec:
  configuration:
    workspace: "{workspace_name}"
    environment: "{environment_config}"

  stages:
    - name: "{stage_name}"
      provisioner: "{provisioner}"
      depends_on: []
      tasks:
        - name: "{task_name}"
          module: "@{repo_name}/path/to/module"
EOF

# 7. Create audit record template
cat > templates/audit-record-template.yaml << 'EOF'
{
  "deployment_id": "{deployment_id}",
  "deployment_name": "{deployment_name}",
  "customer_id": "{customer_id}",
  "environment": "{environment}",
  "timestamp": "{iso_timestamp}",
  "status": "{status}",
  "manifest_hash": "{sha256_hash}",
  "deployed_by": "{user_email}",
  "approver": "{approver_email}",
  "duration_seconds": "{seconds}",
  "stages": [],
  "compliance_checks": []
}
EOF

# 8. Create README
cat > README.md << 'EOF'
# log-int-deploy

Audit sink repository for deployment manifests and compliance records.

## Purpose

- Central archive of all deployment manifests (immutable, versioned)
- Compliance and audit trail records
- Logging infrastructure (Elasticsearch, audit trail)
- Deployment history and compliance reporting

## Archive Structure

- `archive/manifests/` - Versioned deployment YAML files
- `archive/audit-records/` - Audit trail JSON records
- `archive/landscape/` - Landscape bootstrap history
- `archive/zones/` - Per-zone deployment history
- `archive/customers/` - Per-customer deployment history

## Accessing Deployment History

All deployments are indexed in Elasticsearch for querying and compliance reports.

```bash
# Query all deployments for a customer
curl -s http://log-int-deploy:9200/deployments/_search?q=customer_id:customer-001

# Query failed deployments
curl -s http://log-int-deploy:9200/deployments/_search?q=status:failed
```
EOF

# 9. Create .gitignore for sensitive data
cat > .gitignore << 'EOF'
# Don't commit actual secrets
secrets.yaml
*.pem
*.key
*.encrypted

# Terraform artifacts
.terraform/
*.tfstate
*.tfstate.*
.terraform.lock.hcl
EOF

# 10. Commit initial structure
git add .
git commit -m "Initial log-int-deploy repository structure"
```

---

### Phase 3: Link Repositories Together

```bash
# 1. From iac-int-deploy, register log-int-deploy
cd ../iac-int-deploy
strata repo add --name log-int-deploy --path ../log-int-deploy

# 2. Verify repositories are linked
strata repo list

# 3. Test validation
strata validate landscape/bootstrap/bootstrap-deploy.yaml --deep

# 4. Create combined workspace for both repos
cat > .strata/solution.json << 'EOF'
{
  "repositories": {
    "iac-int-deploy": {
      "path": ".",
      "type": "strata_config"
    },
    "log-int-deploy": {
      "path": "../log-int-deploy",
      "type": "strata_audit"
    }
  }
}
EOF
```

---

### Phase 4: Verify Setup

```bash
# 1. Check workspace is ready
cd iac-int-deploy
strata sln status

# 2. Validate bootstrap manifest
strata validate landscape/bootstrap/bootstrap-deploy.yaml --deep

# 3. Create activation profiles
strata profile create --name dev --description "Development environment"
strata profile activate --name dev

# 4. Dry-run bootstrap (will fail with actual provisioner, but validates manifest)
strata build run -f landscape/bootstrap/bootstrap-deploy.yaml --dry-run

# Expected output shows manifest is valid, ready for actual deployment
```

---

## Hierarchical Bootstrap & Deployment Levels

The `iac-int-deploy` repository organizes configurations and deployments across four hierarchical levels, each with its own bootstrap and deployment lifecycle:

### Level 1: Landscape (One-Time Foundational Setup)

**Purpose**: Global platform foundation that exists once across all regions.

**Includes**:
- Global DNS zone management
- Central secrets vault
- Global IAM policies and roles
- Cross-region networking (transit gateways, VPN)
- Global monitoring and logging aggregation
- Shared service mesh configuration

**Bootstrap**: Runs once, typically before any zones are created.

### Level 2: Zone (One Per Azure Region)

**Purpose**: Regional infrastructure linked to a central hub, enabling multi-region deployments.

**Includes**:
- Regional VPC/VNet and subnets
- Regional Kubernetes cluster (regional hub)
- Regional database replicas
- Regional load balancers and firewalls
- Zone-specific monitoring and logging
- Connection to landscape hub

**Bootstrap**: Runs once per zone when establishing a new region.

### Level 3: Customer (Customer-Specific Foundation)

**Purpose**: Tenant isolation and customer-specific baseline configuration.

**Includes**:
- Customer namespace/resource group
- Customer-specific IAM and RBAC
- Customer secrets and API keys
- Customer compliance and audit policies
- Customer resource quotas and limits
- Customer-specific network policies

**Bootstrap**: Runs once per customer when onboarding.

### Level 4: Customer Environment (Customer Dev/Staging/Prod)

**Purpose**: Environment-specific deployments for a single customer.

**Includes**:
- Application deployments
- Environment-specific configurations (dev/staging/prod)
- Database instances for the customer environment
- Load balancer routing rules
- Monitoring and alerting specific to customer

**Deploy**: Runs every time a new environment is provisioned or updated.

---

## Folder Structure: iac-int-deploy

```
iac-int-deploy/
├── .strata/                           # Workspace state
├── config/                            # Shared configurations
│   ├── policies/                      # Global policy engine configs
│   ├── security/                      # Global RBAC, audit rules
│   ├── templates/                     # YAML templates
│   └── providers/
│       ├── aws-global.yaml           # AWS global config
│       └── azure-global.yaml         # Azure global config
│
├── landscape/                         # LEVEL 1: Foundational (One-time)
│   ├── bootstrap/
│   │   ├── bootstrap-landscape.yaml  # Strata workspace definition
│   │   ├── bootstrap-deploy.yaml     # Deploy manifest for bootstrap
│   │   └── policies/
│   │       ├── global-policies.yaml
│   │       └── compliance-baseline.yaml
│   ├── environments/
│   │   ├── global.yaml              # Global environment config
│   │   └── network-peering.yaml     # Multi-region connectivity
│   ├── scripts/
│   │   ├── bootstrap.sh
│   │   └── validate-landscape.sh
│   └── workspaces/
│       └── landscape-ws.yaml         # Workspace definition
│
├── zones/                             # LEVEL 2: Regional (One per region)
│   ├── zone-001/                     # Example: us-east-1
│   │   ├── bootstrap/
│   │   │   ├── bootstrap-zone.yaml
│   │   │   ├── bootstrap-deploy.yaml
│   │   │   ├── zone-config.yaml      # Zone-specific parameters
│   │   │   └── network-config.yaml
│   │   ├── environments/
│   │   │   ├── zone-dev.yaml        # Zone dev environment
│   │   │   ├── zone-staging.yaml    # Zone staging environment
│   │   │   └── zone-prod.yaml       # Zone prod environment
│   │   ├── deploy/
│   │   │   ├── deploy-zone-dev.yaml
│   │   │   ├── deploy-zone-staging.yaml
│   │   │   └── deploy-zone-prod.yaml
│   │   ├── scripts/
│   │   │   └── bootstrap-zone.sh
│   │   └── workspaces/
│   │       ├── zone-001-ws.yaml
│   │       └── zone-001-peering.yaml
│   │
│   ├── zone-002/                     # Example: eu-west-1
│   │   └── (same structure as zone-001)
│   │
│   └── zone-template/                # Template for new zones
│       ├── bootstrap/
│       ├── environments/
│       ├── deploy/
│       └── README.md
│
├── customers/                         # LEVEL 3 & 4: Customer & Environment
│   ├── config/
│   │   ├── policies/                 # Customer onboarding policies
│   │   └── templates/                # Customer bootstrap templates
│   │
│   ├── {customer-number}/            # Example: customer-001
│   │   ├── config/                   # LEVEL 3: Customer bootstrap
│   │   │   ├── customer-config.yaml
│   │   │   ├── customer-rbac.yaml
│   │   │   ├── secrets.yaml          # Encrypted
│   │   │   ├── compliance.yaml
│   │   │   └── quotas.yaml
│   │   │
│   │   ├── dev/                      # LEVEL 4: Dev environment
│   │   │   ├── config/
│   │   │   │   ├── environment.yaml
│   │   │   │   ├── networking.yaml
│   │   │   │   └── applications.yaml
│   │   │   ├── deploy/
│   │   │   │   ├── deploy-dev.yaml
│   │   │   │   └── scripts/
│   │   │   └── workspaces/
│   │   │       └── customer-001-dev-ws.yaml
│   │   │
│   │   ├── staging/                  # LEVEL 4: Staging environment
│   │   │   ├── config/
│   │   │   ├── deploy/
│   │   │   │   └── deploy-staging.yaml
│   │   │   └── workspaces/
│   │   │       └── customer-001-staging-ws.yaml
│   │   │
│   │   ├── production/               # LEVEL 4: Production environment
│   │   │   ├── config/
│   │   │   ├── deploy/
│   │   │   │   └── deploy-production.yaml
│   │   │   └── workspaces/
│   │   │       └── customer-001-prod-ws.yaml
│   │   │
│   │   └── README.md                 # Customer documentation
│   │
│   ├── {customer-number}/            # Example: customer-002
│   │   └── (same structure)
│   │
│   └── customer-template/            # Template for new customers
│       ├── config/
│       ├── dev/
│       ├── staging/
│       ├── production/
│       └── README.md
│
└── workspaces/                        # LEVEL: Workspace definitions
    ├── landscape-workspace.yaml      # Level 1: Landscape workspace
    ├── zone-workspace-template.yaml  # Level 2: Zone workspace template
    ├── customer-bootstrap-ws.yaml    # Level 3: Customer bootstrap workspace
    └── customer-env-ws-template.yaml # Level 4: Environment workspace
```

---

## Repository Architecture

### Configuration Repository: `iac-int-deploy`

Central governance and orchestration engine. Combines all strata configurations, deployment manifests, and policies organized hierarchically by bootstrap level and customer.

```
iac-int-deploy/
├── .strata/                      # Workspace state
├── config/
│   ├── policies/                 # Global policy engine configurations
│   ├── security/                 # Global RBAC, audit, compliance rules
│   ├── templates/                # YAML templates for deployments
│   └── providers/                # Cloud provider configs
│       ├── aws-global.yaml
│       └── azure-global.yaml
├── landscape/                    # LEVEL 1: Foundational (One-time)
│   ├── bootstrap/
│   ├── environments/
│   ├── workspaces/
│   └── scripts/
├── zones/                        # LEVEL 2: Regional (One per region)
│   ├── zone-001/
│   ├── zone-002/
│   └── zone-template/
├── customers/                    # LEVEL 3 & 4: Tenants & Environments
│   ├── config/
│   ├── {customer-number}/
│   │   ├── config/              # LEVEL 3: Customer bootstrap
│   │   ├── dev/                 # LEVEL 4: Dev environment
│   │   ├── staging/             # LEVEL 4: Staging environment
│   │   └── production/          # LEVEL 4: Production environment
│   └── customer-template/
└── workspaces/                   # Workspace definitions
    ├── landscape-workspace.yaml
    ├── zone-workspace-template.yaml
    ├── customer-bootstrap-ws.yaml
    └── customer-env-ws-template.yaml
```

**Key Responsibilities:**
- Define deployment topology & stages with cross-repo references
- Enforce policy compliance and governance
- Orchestrate multi-repo deployments through strata
- Manage promotion workflows (dev → staging → prod)
- Store all strata configuration, policies, and secrets
- Organize deployments hierarchically by bootstrap level and tenant

### Deployment Repository 1: `tf-int-main`

Main Terraform code for core infrastructure (networking, compute, storage).

```
tf-int-main/
├── terraform/
│   ├── main.tf                   # Root module
│   ├── variables.tf              # Input variables
│   ├── outputs.tf                # Outputs
│   ├── providers.tf              # Provider configuration
│   ├── networking/
│   │   ├── vpc.tf
│   │   ├── security-groups.tf
│   │   └── route-tables.tf
│   ├── compute/
│   │   ├── eks-cluster.tf
│   │   ├── aks-cluster.tf
│   │   └── node-pools.tf
│   ├── storage/
│   │   ├── databases.tf
│   │   ├── s3-buckets.tf
│   │   └── blob-storage.tf
│   ├── environments/
│   │   ├── dev.tfvars
│   │   ├── staging.tfvars
│   │   └── production.tfvars
│   └── backends/
│       ├── dev-backend.tf
│       ├── staging-backend.tf
│       └── production-backend.tf
├── scripts/
│   ├── terraform-init.sh
│   ├── terraform-plan.sh
│   └── terraform-apply.sh
└── README.md
```

**Key Responsibilities:**
- Core infrastructure provisioning
- VPC, subnets, security groups
- Kubernetes clusters (EKS, AKS, GKE)
- Managed databases and storage
- Cross-environment Terraform state management

### Deployment Repository 2: `tfmod-int-shared`

Reusable Terraform modules library for common infrastructure patterns.

```
tfmod-int-shared/
├── modules/
│   ├── networking/
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   ├── outputs.tf
│   │   └── examples/
│   ├── compute/
│   │   ├── eks/
│   │   ├── aks/
│   │   └── gke/
│   ├── database/
│   │   ├── rds/
│   │   ├── cosmosdb/
│   │   └── postgres/
│   ├── storage/
│   │   ├── s3/
│   │   ├── blob-storage/
│   │   └── vault/
│   ├── observability/
│   │   ├── prometheus/
│   │   ├── datadog/
│   │   └── elk/
│   └── security/
│       ├── waf/
│       ├── firewall/
│       └── secrets-mgmt/
├── tests/
│   ├── networking_test.tf
│   ├── compute_test.tf
│   └── storage_test.tf
└── README.md
```

**Key Responsibilities:**
- Provide reusable Terraform modules
- Ensure consistent infrastructure patterns across environments
- Module testing and validation
- Version management and compatibility

---

### Deployment Repository 3: `ans-int-config`

Server configuration and orchestration using Ansible.

```
ans-int-config/
├── playbooks/
│   ├── provision-servers.yml     # Initial server setup
│   ├── configure-security.yml    # Security hardening
│   ├── setup-monitoring.yml      # Deploy monitoring agents
│   ├── patch-systems.yml         # OS patching
│   └── backup-config.yml         # Backup configuration
├── roles/
│   ├── base/                     # Base OS configuration
│   │   ├── tasks/
│   │   ├── handlers/
│   │   └── templates/
│   ├── docker/                   # Docker installation and setup
│   ├── kubernetes-node/          # Kubernetes node configuration
│   ├── logging-agent/            # Fluent-bit, Filebeat setup
│   ├── security-hardening/       # CIS benchmarks
│   └── monitoring-agent/         # Prometheus, Datadog agent
├── inventory/
│   ├── dev
│   ├── staging
│   ├── production
│   └── group_vars/
│       ├── all.yml
│       ├── kubernetes_nodes.yml
│       └── database_servers.yml
├── vars/
│   ├── common.yml
│   ├── environment-specific.yml
│   └── secrets.yml (encrypted)
└── scripts/
    ├── ansible-lint.sh
    ├── ansible-syntax-check.sh
    └── ansible-playbook-run.sh
```

**Key Responsibilities:**
- Configure server OS and container runtimes
- Deploy and configure monitoring agents
- Apply security hardening and compliance
- Manage patches and updates
- Post-provisioning automation

---

### Deployment Repository 4: `chart-int-deploy`

Kubernetes workloads, Helm charts, and GitOps configuration.

```
chart-int-deploy/
├── helm/
│   ├── charts/                   # Helm chart definitions
│   │   ├── platform-core/        # Core platform services
│   │   ├── monitoring/           # Prometheus, Grafana stack
│   │   ├── logging/              # ELK stack
│   │   ├── ingress-controller/   # Ingress (nginx/ALB)
│   │   ├── cert-manager/         # TLS certificate management
│   │   ├── argocd/               # GitOps controller
│   │   └── security/             # Falco, network policies
│   ├── values/
│   │   ├── dev-values.yaml
│   │   ├── staging-values.yaml
│   │   └── production-values.yaml
│   └── chart-dependencies.lock
├── argocd/
│   ├── applications/
│   │   ├── monitoring-app.yaml
│   │   ├── logging-app.yaml
│   │   ├── ingress-app.yaml
│   │   └── custom-apps.yaml
│   ├── projects/
│   │   ├── core-services.yaml
│   │   └── tenant-services.yaml
│   └── notifications/
│       └── notification-config.yaml
├── kustomize/
│   ├── base/
│   │   ├── kustomization.yaml
│   │   └── patches/
│   ├── overlays/
│   │   ├── dev/
│   │   ├── staging/
│   │   └── production/
│   └── components/
└── scripts/
    ├── helm-lint.sh
    ├── helm-template.sh
    └── argocd-sync.sh
```

**Key Responsibilities:**
- Kubernetes workload deployment via Helm
- GitOps workflow with ArgoCD
- Chart versioning and dependency management
- Multi-environment Helm values
- Platform service orchestration

---

### Deployment Repository 5: `log-int-deploy`

Centralized audit logging, deployment manifest storage, and observability infrastructure. Serves as the **audit sink** where all deployment manifests and audit data are pushed and archived.

```
log-int-deploy/
├── terraform/
│   ├── main.tf                   # Logging infrastructure
│   ├── elasticsearch-cluster.tf
│   ├── opensearch-domain.tf
│   ├── log-storage.tf            # S3, blob storage for logs & manifests
│   ├── audit-trail.tf            # Audit trail infrastructure
│   └── backends/
│       ├── dev-backend.tf
│       ├── staging-backend.tf
│       └── production-backend.tf
├── helm/
│   ├── filebeat-daemonset.yaml   # Log collection
│   ├── fluentd-config.yaml       # Log processing
│   ├── elasticsearch-values.yaml
│   ├── kibana-values.yaml
│   ├── logstash-config.yaml      # Manifest ingestion pipeline
│   └── datadog-agent.yaml
├── ansible/
│   ├── configure-logging.yml     # Logging agent setup
│   ├── setup-audit.yml           # Audit logging config
│   └── roles/
│       └── log-forwarding/
├── archive/                       # Deployment manifest & audit data archive
│   ├── landscape/
│   │   └── bootstrap-deploys/    # Landscape bootstrap manifests
│   ├── zones/
│   │   └── zone-001/deploy-history/
│   ├── customers/
│   │   └── customer-001/
│   │       ├── bootstrap-history/
│   │       ├── prod-deploys/
│   │       └── audit-logs/
│   ├── manifests/                # Versioned deployment manifests
│   └── audit-records/            # Compliance & audit trails
├── policies/
│   ├── log-retention.yaml        # Retention policies
│   ├── log-classification.yaml   # Data classification
│   ├── audit-compliance.yaml     # Compliance requirements
│   └── manifest-ingestion.yaml   # Rules for processing deployment manifests
├── scripts/
│   ├── elasticsearch-init.sh
│   ├── opensearch-init.sh
│   ├── log-ingestion-setup.sh
│   ├── manifest-archiver.sh      # Archive deployment manifests
│   └── audit-reporter.sh         # Generate audit reports
├── dashboards/
│   ├── security-dashboard.json
│   ├── audit-dashboard.json
│   ├── deployment-history.json   # Track deployment manifests
│   └── compliance-dashboard.json
└── templates/
    ├── manifest-template.yaml    # Standard for logged manifests
    └── audit-record-template.yaml
```

**Key Responsibilities:**
- Deploy and manage centralized logging infrastructure
- Elasticsearch/OpenSearch cluster provisioning for audit data and logs
- Log collection agent configuration
- **Audit sink for all deployment manifests** — capture and archive every deployment
- **Deployment manifest versioning** — track all applied configurations
- Audit trail management and compliance record keeping
- Log retention and archival policies
- Generate compliance reports from deployment history

---

## Deployment Flow

### Deployment Strategy by Level

#### Level 1: Landscape Bootstrap (Foundation - One Time)
```yaml
stage: landscape-setup
provisioner: terraform
scope: global
on_failure: stop
tasks:
  - name: global-networking
    module: "@tfmod-int-shared/modules/networking"
    config: "@iac-int-deploy/landscape/bootstrap/network-setup.yaml"
  
  - name: global-secrets
    provisioner: terraform
    source: "@tf-int-main/terraform/secrets-vault"
  
  - name: global-monitoring
    provisioner: helm
    chart: "@chart-int-deploy/helm/charts/monitoring"
```

**When to Run**: Before first zone is deployed  
**Idempotency**: Must be idempotent — safe to run multiple times  
**Rollback**: Critical — test thoroughly before production

---

#### Level 2: Zone Bootstrap (Regional - One Per Region)
```yaml
stage: zone-setup
provisioner: terraform
scope: regional
on_failure: stop
depends_on: [landscape-setup]
tasks:
  - name: regional-vpc
    module: "@tfmod-int-shared/modules/networking"
    config: "@iac-int-deploy/zones/{zone-id}/bootstrap/network-config.yaml"
  
  - name: regional-cluster
    module: "@tfmod-int-shared/modules/compute/eks"
    config: "@iac-int-deploy/zones/{zone-id}/bootstrap/cluster-config.yaml"
  
  - name: zone-to-landscape-peering
    module: "@tfmod-int-shared/modules/networking/peering"
```

**When to Run**: When establishing a new region  
**Repetition**: Once per zone, not per customer  
**Link to Landscape**: Must connect to landscape hub via VPN/peering

---

#### Level 3: Customer Bootstrap (Tenant Isolation)
```yaml
stage: customer-setup
provisioner: terraform
scope: per-customer
on_failure: stop
depends_on: [zone-setup]
tasks:
  - name: customer-namespace
    provisioner: terraform
    config: "@iac-int-deploy/customers/{customer-number}/config/namespace.yaml"
  
  - name: customer-rbac
    provisioner: terraform
    config: "@iac-int-deploy/customers/{customer-number}/config/rbac.yaml"
  
  - name: customer-secrets
    provisioner: terraform
    config: "@iac-int-deploy/customers/{customer-number}/config/secrets.yaml"
```

**When to Run**: When onboarding a new customer  
**Isolation**: Complete tenant isolation achieved at this level  
**Reusable**: Templated from `customer-template/`

---

#### Level 4: Customer Environment Deploy (App Environments)
```yaml
stage: customer-environment-deploy
provisioner: terraform+helm
scope: per-customer-environment
on_failure: continue
depends_on: [customer-setup]
tasks:
  - name: environment-database
    provisioner: terraform
    config: "@iac-int-deploy/customers/{customer-number}/{env}/config/database.yaml"
  
  - name: environment-applications
    provisioner: helm
    config: "@iac-int-deploy/customers/{customer-number}/{env}/config/apps.yaml"
  
  - name: environment-monitoring
    provisioner: helm
    chart: "@chart-int-deploy/helm/charts/monitoring"
    values: "@iac-int-deploy/customers/{customer-number}/{env}/config/monitoring-values.yaml"
```

**When to Run**: Every environment change (dev/staging/prod)  
**Frequency**: Multiple times (per environment per customer)  
**Promotion**: Dev → Staging → Prod

---

### Stage 1: Networking

Networking stages exist at multiple levels:

- **Landscape Networking**: Global peering, transit gateways, DNS zones
- **Zone Networking**: Regional VPC, subnets, zone-to-landscape peering
- **Customer Networking**: Customer network policies, internal routing

```yaml
stage: networking
provisioner: terraform
dependencies: []
tasks:
  - provision VPC & subnets
  - configure security groups
  - setup DNS zones
```

### Stage 2: Compute
```yaml
stage: compute
provisioner: terraform
dependencies: [networking]
tasks:
  - provision Kubernetes cluster (EKS/AKS/GKE)
  - configure node groups
  - setup autoscaling
```

### Stage 3: Storage & Databases
```yaml
stage: persistence
provisioner: terraform
dependencies: [networking]
tasks:
  - provision managed databases (RDS/CosmosDB)
  - configure storage buckets
  - setup backups
```

### Stage 4: Platform Services
```yaml
stage: platform-services
provisioner: helm
dependencies: [compute]
tasks:
  - deploy ingress controller
  - deploy cert-manager
  - deploy monitoring stack
```

### Stage 5: Configuration
```yaml
stage: configuration
provisioner: ansible
dependencies: [platform-services]
tasks:
  - configure security policies
  - deploy logging agents
  - setup compliance controls
```

---

---

## Bootstrap & Deployment Manifest Examples

### Landscape Bootstrap Manifest

**File**: `landscape/bootstrap/bootstrap-deploy.yaml`

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: landscape-bootstrap
  annotations:
    description: "One-time platform foundation setup"
    tier: landscape
    run_frequency: "once"

spec:
  configuration:
    workspace: landscape-workspace
    environment: landscape
  
  stages:
    - name: global-networking
      provisioner: terraform-main
      scope: global
      on_failure: stop
      tasks:
        - name: transit-gateway
          module: "@tfmod-int-shared/modules/networking/transit"
        - name: global-dns
          module: "@tfmod-int-shared/modules/networking/dns"
    
    - name: global-secrets-vault
      provisioner: terraform-main
      depends_on: [global-networking]
      tasks:
        - source: "@tf-int-main/terraform/secrets-vault"
    
    - name: global-monitoring
      provisioner: helm-charts
      depends_on: [global-secrets-vault]
      tasks:
        - chart: "@chart-int-deploy/helm/charts/monitoring"
          values_file: "@iac-int-deploy/landscape/environments/monitoring-values.yaml"
```

### Zone Bootstrap Manifest

**File**: `zones/zone-001/bootstrap/bootstrap-deploy.yaml`

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: zone-001-bootstrap
  annotations:
    description: "Zone setup for us-east-1 region"
    zone_id: "zone-001"
    region: "us-east-1"
    run_frequency: "once_per_zone"

spec:
  configuration:
    workspace: zone-001-workspace
    environment: "@iac-int-deploy/zones/zone-001/environments/zone-prod.yaml"
  
  stages:
    - name: regional-vpc
      provisioner: terraform-main
      scope: regional
      on_failure: stop
      tasks:
        - name: vpc-setup
          module: "@tfmod-int-shared/modules/networking"
          config: "@iac-int-deploy/zones/zone-001/bootstrap/zone-config.yaml"
    
    - name: regional-cluster
      provisioner: terraform-main
      depends_on: [regional-vpc]
      tasks:
        - name: eks-cluster
          module: "@tfmod-int-shared/modules/compute/eks"
          config: "@iac-int-deploy/zones/zone-001/bootstrap/cluster-config.yaml"
    
    - name: zone-to-landscape-peering
      provisioner: terraform-main
      depends_on: [regional-vpc]
      tasks:
        - module: "@tfmod-int-shared/modules/networking/peering"
          config: "@iac-int-deploy/zones/zone-001/bootstrap/peering-config.yaml"
    
    - name: zone-platform-services
      provisioner: helm-charts
      depends_on: [regional-cluster]
      tasks:
        - chart: "@chart-int-deploy/helm/charts/platform-core"
          values_file: "@iac-int-deploy/zones/zone-001/environments/platform-values.yaml"
```

### Customer Bootstrap Manifest

**File**: `customers/customer-001/config/bootstrap.yaml`

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: customer-001-bootstrap
  annotations:
    description: "Tenant isolation setup for customer-001"
    customer_id: "customer-001"
    run_frequency: "once_per_customer"

spec:
  configuration:
    workspace: customer-001-bootstrap-workspace
    environment: "@iac-int-deploy/customers/customer-001/config/bootstrap-config.yaml"
  
  stages:
    - name: customer-namespace
      provisioner: terraform-main
      scope: per-customer
      on_failure: stop
      tasks:
        - module: "@tfmod-int-shared/modules/namespace"
          config: "@iac-int-deploy/customers/customer-001/config/namespace.yaml"
    
    - name: customer-rbac
      provisioner: terraform-main
      depends_on: [customer-namespace]
      tasks:
        - module: "@tfmod-int-shared/modules/rbac"
          config: "@iac-int-deploy/customers/customer-001/config/rbac.yaml"
    
    - name: customer-secrets
      provisioner: terraform-main
      depends_on: [customer-namespace]
      tasks:
        - source: "@iac-int-deploy/customers/customer-001/config/secrets.yaml"
    
    - name: customer-compliance
      provisioner: terraform-main
      depends_on: [customer-rbac]
      tasks:
        - module: "@tfmod-int-shared/modules/compliance"
          config: "@iac-int-deploy/customers/customer-001/config/compliance.yaml"
```

### Customer Environment Deploy Manifest

**File**: `customers/customer-001/production/deploy/deploy-production.yaml`

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: customer-001-prod-deploy
  annotations:
    description: "Production environment for customer-001"
    customer_id: "customer-001"
    environment: production
    run_frequency: "continuous"

spec:
  configuration:
    workspace: customer-001-prod-workspace
    environment: "@iac-int-deploy/customers/customer-001/production/config/environment.yaml"
  
  stages:
    - name: production-database
      provisioner: terraform-main
      scope: per-customer-environment
      on_failure: stop
      tasks:
        - name: postgres-primary
          module: "@tfmod-int-shared/modules/database/postgres"
          config: "@iac-int-deploy/customers/customer-001/production/config/database.yaml"
    
    - name: production-applications
      provisioner: helm-charts
      depends_on: [production-database]
      tasks:
        - name: customer-apps
          chart: "@chart-int-deploy/helm/charts/customer-workloads"
          values_file: "@iac-int-deploy/customers/customer-001/production/config/apps-values.yaml"
    
    - name: production-monitoring
      provisioner: helm-charts
      depends_on: [production-applications]
      tasks:
        - chart: "@chart-int-deploy/helm/charts/monitoring"
          values_file: "@iac-int-deploy/customers/customer-001/production/config/monitoring-values.yaml"
    
    - name: production-backup
      provisioner: terraform-main
      depends_on: [production-database]
      tasks:
        - module: "@tfmod-int-shared/modules/backup"
          config: "@iac-int-deploy/customers/customer-001/production/config/backup-policy.yaml"
```

---

## Cross-Repository References

All repositories are coordinated through `iac-int-deploy` which uses strata's `@repo/path` notation to reference external repos:

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: integrator-prod
spec:
  configuration: "@iac-int-deploy/config/production"
  provisioners:
    - name: terraform-main
      repository: tf-int-main
    - name: terraform-modules
      repository: tfmod-int-shared
    - name: ansible-config
      repository: ans-int-config
    - name: helm-charts
      repository: chart-int-deploy
    - name: logging-infra
      repository: log-int-deploy
  
  stages:
    - name: infrastructure
      provisioner: terraform
      tasks:
        - module: "@tfmod-int-shared/modules/networking"
        - module: "@tfmod-int-shared/modules/compute"
    
    - name: platform-services
      provisioner: helm
      tasks:
        - chart: "@chart-int-deploy/helm/charts/monitoring"
        - chart: "@chart-int-deploy/helm/charts/logging"
    
    - name: logging-stack
      provisioner: terraform
      tasks:
        - source: "@log-int-deploy/terraform"
    
    - name: server-config
      provisioner: ansible
      tasks:
        - playbook: "@ans-int-config/playbooks/provision-servers.yml"
```

---

## Deployment Manifest & Audit Data Flow

All deployment manifests and audit records are pushed to `log-int-deploy` as a centralized audit sink. This creates an immutable, versioned record of every deployment.

### Manifest Archiving Process

**1. Pre-Deployment: Manifest Capture**
```bash
# Before running deployment, capture manifest
strata build run -f customers/customer-001/production/deploy/deploy-production.yaml
# Generated: platform.json (resolved configuration)
# Captured: deployment manifest snapshot
```

**2. Deployment Execution with Audit Logging**
```yaml
# In deployment manifest:
spec:
  audit:
    repository: log-int-deploy
    archive_path: "archive/customers/customer-001/production"
    record_type: "deployment-manifest"
    tags:
      - customer-id: customer-001
      - environment: production
      - deployment-date: "2026-07-06"
```

**3. Post-Deployment: Archive to log-int-deploy**
```bash
# Manifest is automatically pushed to log-int-deploy
# Location: log-int-deploy/archive/customers/customer-001/production/
# Files archived:
# - deployment-2026-07-06-14-32-45.yaml (applied manifest)
# - platform.json (resolved values)
# - audit-log-2026-07-06-14-32-45.json (execution log)
```

### Audit Record Structure

**File**: `log-int-deploy/archive/customers/customer-001/production/audit-log-2026-07-06.json`

```json
{
  "deployment_id": "deploy-prod-20260706-143245",
  "deployment_name": "customer-001-prod-deploy",
  "customer_id": "customer-001",
  "environment": "production",
  "zone": "zone-001",
  "timestamp": "2026-07-06T14:32:45Z",
  "status": "success",
  "manifest_hash": "sha256:abc123...",
  "changed_resources": [
    {
      "type": "kubernetes_deployment",
      "name": "customer-001-api",
      "action": "create",
      "diff": "..."
    }
  ],
  "executed_by": "deployer@omp.com",
  "approver": "ops-lead@omp.com",
  "duration_seconds": 287,
  "stages": [
    {
      "name": "production-database",
      "status": "success",
      "duration": 45
    },
    {
      "name": "production-applications",
      "status": "success",
      "duration": 120
    }
  ],
  "compliance_checks": [
    {
      "policy": "security-baseline",
      "result": "passed"
    },
    {
      "policy": "audit-logging",
      "result": "passed"
    }
  ]
}
```

### Querying Deployment History

**Via Elasticsearch in log-int-deploy:**

```bash
# All deployments for a customer
curl -X GET "log-int-deploy:9200/deployments/_search?q=customer_id:customer-001"

# Deployments in last 24 hours
curl -X GET "log-int-deploy:9200/deployments/_search" -d '{
  "query": {
    "range": {
      "timestamp": {
        "gte": "now-24h"
      }
    }
  }
}'

# Failed deployments
curl -X GET "log-int-deploy:9200/deployments/_search?q=status:failed"
```

**Via Kibana Dashboard:**
- Navigate to `log-int-deploy` Kibana instance
- View `deployment-history` dashboard
- Filter by: customer, environment, date range, status

### Compliance & Audit Reports

**Generate compliance report from archived manifests:**

```bash
# Script: log-int-deploy/scripts/compliance-reporter.sh
./compliance-reporter.sh \
  --customer customer-001 \
  --date-range "2026-06-01 to 2026-07-06" \
  --output compliance-report.pdf

# Report includes:
# - All deployments executed
# - Compliance checks passed/failed
# - Configuration changes
# - Security policy violations (if any)
# - Approval chain
```

---

## Environment Progression Strategy

```
┌─────────────────────────────────────────────────────────────┐
│                    PROMOTION WORKFLOW                        │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Developer Branch  →  Dev Environment  →  Staging  →  Prod │
│                                                              │
│  - Feature tests    - Integration    - Soak tests  - Live  │
│  - Unit tests       - E2E tests      - Performance - Hot   │
│  - Code review      - Smoke tests    - Security   - Blue/  │
│                     - Compliance     - Compliance  Green   │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## Security & Compliance Model

### Secret Management
- **Development**: Local file-based secrets (encrypted)
- **Staging/Production**: HashiCorp Vault or Azure Key Vault
- **Rotation**: Automated monthly for production credentials

### Access Control
- RBAC enforced at strata level
- Multi-factor authentication required for production deployments
- Audit trail of all infrastructure changes

### Compliance
- Policy engine validates all deployments against company standards
- Automated compliance scanning post-deployment
- Regular drift detection and remediation

---

## Strata Configuration Example

### Workspace Definition (in `iac-int-deploy`)

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: workspace
meta:
  name: integrator-platform
  annotations:
    description: "OMP Integrator multi-tenant SaaS platform"
    contact: platform-team@omp.com
  labels:
    environment: production
    tier: platform

spec:
  provisioners:
    - name: terraform-main
      provisioner: terraform
      backend:
        type: terraform_cloud
        configuration:
          organization: omp-integrator
          workspace: integrator-prod
    
    - name: terraform-modules
      provisioner: terraform
      source:
        repository: tfmod-int-shared
    
    - name: helm-charts
      provisioner: helm
      configuration:
        cluster_endpoint_secret: k8s_endpoint
        cluster_ca_secret: k8s_ca_cert
    
    - name: ansible-provisioner
      provisioner: ansible
      configuration:
        ssh_private_key_secret: integrator_ssh_key
    
    - name: logging
      provisioner: terraform
      source:
        repository: log-int-deploy
  
  secrets:
    - key: aws_access_key
      source: vault
      path: secret/integrator/aws-prod
    - key: k8s_endpoint
      source: vault
      path: secret/integrator/k8s-prod
    - key: k8s_ca_cert
      source: vault
      path: secret/integrator/k8s-ca
    - key: integrator_ssh_key
      source: vault
      path: secret/integrator/ssh-key
```

### Solution Configuration (in `iac-int-deploy`)

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: integrator-prod-config
  annotations:
    description: "Production configuration for integrator"
    owner: platform-operations

spec:
  repositories:
    - name: tf-int-main
      url: https://github.com/omp/tf-int-main.git
      branch: main
    
    - name: tfmod-int-shared
      url: https://github.com/omp/tfmod-int-shared.git
      branch: main
    
    - name: ans-int-config
      url: https://github.com/omp/ans-int-config.git
      branch: main
    
    - name: chart-int-deploy
      url: https://github.com/omp/chart-int-deploy.git
      branch: main
    
    - name: log-int-deploy
      url: https://github.com/omp/log-int-deploy.git
      branch: main
```

### Deployment Definition (in `iac-int-deploy`)

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: integrator-prod-deploy
  annotations:
    description: "Production deployment for integrator platform"
    owner: platform-operations
  labels:
    environment: production

spec:
  configuration:
    workspace: integrator-platform
    environment: production
  
  stages:
    - name: networking
      provisioner: terraform-main
      scope: all
      on_failure: stop
      tasks:
        - name: vpc-setup
          module: "@tfmod-int-shared/modules/networking"
    
    - name: compute
      provisioner: terraform-main
      depends_on: [networking]
      scope: all
      tasks:
        - name: eks-cluster
          module: "@tfmod-int-shared/modules/compute/eks"
    
    - name: logging
      provisioner: logging
      depends_on: [compute]
      scope: all
      tasks:
        - source: "@log-int-deploy/terraform"
    
    - name: server-provisioning
      provisioner: ansible-provisioner
      depends_on: [compute]
      scope: all
      tasks:
        - name: security-hardening
          playbook: "@ans-int-config/playbooks/configure-security.yml"
    
    - name: platform-services
      provisioner: helm-charts
      depends_on: [server-provisioning, logging]
      scope: all
      tasks:
        - name: monitoring-stack
          chart: "@chart-int-deploy/helm/charts/monitoring"
          values_file: "@iac-int-deploy/config/helm/monitoring-values.yaml"
        
        - name: logging-stack
          chart: "@chart-int-deploy/helm/charts/logging"
          values_file: "@iac-int-deploy/config/helm/logging-values.yaml"
        
        - name: argocd
          chart: "@chart-int-deploy/helm/charts/argocd"
          values_file: "@iac-int-deploy/config/helm/argocd-values.yaml"
```

---

---

## Bootstrap Execution Order & Dependencies

The hierarchical levels must be deployed in strict order. Each level depends on the previous level being complete.

```
┌──────────────────────────────────────────────────────────────┐
│ Level 1: Landscape Bootstrap                                 │
│ (Global DNS, Secrets Vault, Central Monitoring)              │
│ ⬇️  Run: strata deploy run -f landscape/bootstrap/             │
└──────────────────────────────────────────────────────────────┘
                              ⬇️
┌──────────────────────────────────────────────────────────────┐
│ Level 2: Zone Bootstrap (Repeat per region)                   │
│ (Regional VPC, Cluster, Zone-to-Landscape Peering)           │
│ ⬇️  Run: strata deploy run -f zones/zone-001/bootstrap/       │
│          strata deploy run -f zones/zone-002/bootstrap/       │
└──────────────────────────────────────────────────────────────┘
                              ⬇️
┌──────────────────────────────────────────────────────────────┐
│ Level 3: Customer Bootstrap (Repeat per customer)             │
│ (Namespace, RBAC, Secrets, Compliance)                       │
│ ⬇️  Run: strata deploy run -f customers/001/config/           │
│          strata deploy run -f customers/002/config/           │
└──────────────────────────────────────────────────────────────┘
                              ⬇️
┌──────────────────────────────────────────────────────────────┐
│ Level 4: Customer Environment Deploy (Repeat per env)         │
│ (Databases, Apps, Monitoring — dev/staging/prod)            │
│ ⬇️  Run: strata deploy run -f customers/001/dev/deploy/       │
│          strata deploy run -f customers/001/staging/deploy/   │
│          strata deploy run -f customers/001/prod/deploy/      │
└──────────────────────────────────────────────────────────────┘
```

### Bootstrap Checklist

```bash
# 1. Landscape (ONE TIME)
strata validate landscape/bootstrap/bootstrap-deploy.yaml --deep
strata build run -f landscape/bootstrap/bootstrap-deploy.yaml
strata deploy run -f landscape/bootstrap/bootstrap-deploy.yaml --force

# 2. First Zone
strata validate zones/zone-001/bootstrap/bootstrap-deploy.yaml --deep
strata build run -f zones/zone-001/bootstrap/bootstrap-deploy.yaml
strata deploy run -f zones/zone-001/bootstrap/bootstrap-deploy.yaml --force

# 3. Additional Zones (repeat for each zone)
strata deploy run -f zones/zone-002/bootstrap/bootstrap-deploy.yaml --force

# 4. New Customer (repeat for each customer)
strata validate customers/customer-001/config/bootstrap.yaml --deep
strata deploy run -f customers/customer-001/config/bootstrap.yaml --force

# 5. Customer Environments (repeat for each customer/environment)
strata deploy run -f customers/customer-001/dev/deploy/deploy-dev.yaml --force
strata deploy run -f customers/customer-001/staging/deploy/deploy-staging.yaml --force
strata deploy run -f customers/customer-001/production/deploy/deploy-production.yaml --force
```

---

## Development Workflow

### For Platform Engineers (Landscape & Global Policy)
```bash
# 1. Update global policies or landscape bootstrap
cd iac-int-deploy/
git checkout -b feature/enhance-global-policy
# ... edit landscape/bootstrap/bootstrap-deploy.yaml
# ... or edit config/policies/global-policies.yaml
git push && create PR

# 2. Validate landscape bootstrap
strata validate landscape/bootstrap/bootstrap-deploy.yaml --deep
strata build run -f landscape/bootstrap/bootstrap-deploy.yaml --dry-run

# 3. Merge and deploy (typically one-time, review carefully)
git merge --ff feature/enhance-global-policy
strata deploy run -f landscape/bootstrap/bootstrap-deploy.yaml --dry-run --force
```

### For Regional Infrastructure Engineers (Zone Bootstrap)
```bash
# 1. Add new zone or update existing zone bootstrap
cd iac-int-deploy/
git checkout -b feature/add-zone-002
# ... create zones/zone-002/bootstrap/bootstrap-deploy.yaml
# ... or update existing zones/zone-001/bootstrap/
git push && create PR

# 2. Validate zone bootstrap
strata validate zones/zone-002/bootstrap/bootstrap-deploy.yaml --deep
strata build run -f zones/zone-002/bootstrap/bootstrap-deploy.yaml --dry-run

# 3. Deploy zone
git merge --ff feature/add-zone-002
strata deploy run -f zones/zone-002/bootstrap/bootstrap-deploy.yaml --force
```

### For Customer Onboarding Team (Customer Bootstrap)
```bash
# 1. Onboard new customer
cd iac-int-deploy/
git checkout -b feature/onboard-customer-101
# ... create customers/customer-101/config/ from template
# ... customize config/rbac.yaml, config/secrets.yaml
git push && create PR

# 2. Validate customer bootstrap
strata validate customers/customer-101/config/bootstrap.yaml --deep
strata build run -f customers/customer-101/config/bootstrap.yaml --dry-run

# 3. Deploy customer
git merge --ff feature/onboard-customer-101
strata deploy run -f customers/customer-101/config/bootstrap.yaml --force
```

### For Application Engineers (Customer Environment Deployments)
```bash
# 1. Deploy or update customer environment
cd iac-int-deploy/
git checkout -b feature/customer-101-add-new-service
# ... edit customers/customer-101/production/config/apps-values.yaml
# ... or customers/customer-101/production/deploy/deploy-production.yaml
git push && create PR

# 2. Test in dev environment first
strata validate customers/customer-101/dev/deploy/deploy-dev.yaml --deep
strata build run -f customers/customer-101/dev/deploy/deploy-dev.yaml --dry-run
strata deploy run -f customers/customer-101/dev/deploy/deploy-dev.yaml --force

# 3. Promote to staging
strata deploy run -f customers/customer-101/staging/deploy/deploy-staging.yaml --dry-run
strata deploy run -f customers/customer-101/staging/deploy/deploy-staging.yaml --force

# 4. Merge and deploy to production
git merge --ff feature/customer-101-add-new-service
strata deploy run -f customers/customer-101/production/deploy/deploy-production.yaml --dry-run
strata deploy run -f customers/customer-101/production/deploy/deploy-production.yaml --force
```

### For Infrastructure Engineers (Terraform)
```bash
# 1. Update core infrastructure or modules
cd tf-int-main/
# OR
cd tfmod-int-shared/
git checkout -b feature/eks-auto-scaling

# 2. Validate Terraform code
terraform validate
terraform fmt -check

# 3. Test through strata
cd ../iac-int-deploy/
strata validate deploy/deploy-dev.yaml --deep
strata build run -f deploy/deploy-dev.yaml --dry-run
strata deploy run -f deploy/deploy-dev.yaml --dry-run

# 4. Merge and promote
cd ../tf-int-main/
git merge --ff feature/eks-auto-scaling
```

### For Configuration Engineers (Ansible)
```bash
# 1. Update server configuration playbooks/roles
cd ans-int-config/
git checkout -b feature/security-hardening
# ... edit playbooks/configure-security.yml
# ... or add roles/new-role/

# 2. Lint and validate Ansible code
ansible-lint playbooks/configure-security.yml
ansible-playbook --syntax-check playbooks/configure-security.yml

# 3. Test through strata in dev environment
cd ../iac-int-deploy/
strata validate deploy/deploy-dev.yaml --deep
strata deploy run -f deploy/deploy-dev.yaml --dry-run

# 4. Merge and deploy
git merge --ff feature/security-hardening
```

### For Kubernetes/DevOps Engineers (Helm/ArgoCD)
```bash
# 1. Update Helm charts or ArgoCD configurations
cd chart-int-deploy/
git checkout -b feature/new-monitoring-stack
# ... edit helm/charts/monitoring/
# ... or update argocd/applications/

# 2. Lint and validate Helm
helm lint helm/charts/monitoring/
helm template -f helm/values/production-values.yaml

# 3. Test through strata
cd ../iac-int-deploy/
strata validate deploy/deploy-dev.yaml --deep
strata build run -f deploy/deploy-dev.yaml --dry-run

# 4. Merge and deploy with ArgoCD sync
git merge --ff feature/new-monitoring-stack
# ArgoCD will automatically sync the changes
```

### For Logging/Observability Engineers
```bash
# 1. Update logging infrastructure
cd log-int-deploy/
git checkout -b feature/enhanced-audit-logging
# ... edit terraform/audit-trail.tf
# ... or update policies/audit-compliance.yaml

# 2. Validate Terraform and configurations
terraform validate
strata validate config/policies/audit-compliance.yaml

# 3. Test deployment
cd ../iac-int-deploy/
strata build run -f deploy/deploy-dev.yaml --dry-run
strata deploy run -f deploy/deploy-dev.yaml --dry-run

# 4. Merge and deploy
git merge --ff feature/enhanced-audit-logging
```

### Cross-Repository Promotion (Dev → Staging → Prod)
```bash
# 1. All repos are tested and merged to main
# 2. From central config repo, promote through environments
cd iac-int-deploy/
git checkout main && git pull

# 3. Run complete deployment flow
strata validate deploy/deploy-staging.yaml --deep
strata build run -f deploy/deploy-staging.yaml --dry-run
strata deploy run -f deploy/deploy-staging.yaml --force

# 4. Promote to production
strata validate deploy/deploy-production.yaml --deep
strata build run -f deploy/deploy-production.yaml --dry-run
strata deploy run -f deploy/deploy-production.yaml --force
```

---

## Getting Started

### Prerequisites
- strata CLI installed
- AWS/Azure/GCP credentials configured
- Terraform ≥ 1.0
- Helm ≥ 3.0
- Ansible ≥ 2.9
- Git configured for multi-repo cloning

### Quick Start
```bash
# 1. Clone central config repository (orchestration point)
git clone <iac-int-deploy-repo> iac-int-deploy
cd iac-int-deploy

# 2. Initialize strata workspace
strata sln init

# 3. Register all external deployment repositories
strata repo add --name tf-int-main --path ../tf-int-main
strata repo add --name tfmod-int-shared --path ../tfmod-int-shared
strata repo add --name ans-int-config --path ../ans-int-config
strata repo add --name chart-int-deploy --path ../chart-int-deploy
strata repo add --name log-int-deploy --path ../log-int-deploy

# 4. Or clone all at once using a wrapper script
# Clone in sibling directories:
for repo in tf-int-main tfmod-int-shared ans-int-config chart-int-deploy log-int-deploy; do
  git clone <repo-url> ../$repo
done

# 5. Activate environment profile
strata profile create --name dev
strata profile activate --name dev

# 6. Validate complete deployment
strata validate deploy/deploy-dev.yaml --deep
strata build run -f deploy/deploy-dev.yaml --dry-run

# 7. Deploy to dev environment
strata deploy run -f deploy/deploy-dev.yaml --force
```

### Repository Organization on Disk

Recommended local directory structure:

```
~/integrator-platform/
├── iac-int-deploy/           # Central config & orchestration
│   ├── deploy/
│   ├── config/
│   └── .strata/              # Workspace state
├── tf-int-main/              # Terraform root modules
├── tfmod-int-shared/         # Terraform modules library
├── ans-int-config/           # Ansible playbooks & roles
├── chart-int-deploy/         # Helm charts & ArgoCD
└── log-int-deploy/           # Logging infrastructure
```

---

## Common Operations & Workflows

### Adding a New Zone (Region)

```bash
# 1. Create zone structure from template
cd iac-int-deploy
mkdir -p zones/zone-003/{bootstrap,environments,deploy,workspaces,scripts}
cp -r zones/zone-template/* zones/zone-003/

# 2. Customize zone configuration
# Edit zones/zone-003/bootstrap/zone-config.yaml
# - Set zone_id: zone-003
# - Set region: eu-central-1 (or desired region)
# - Update network CIDR blocks

# 3. Update zone workspace
# Edit zones/zone-003/workspaces/zone-003-ws.yaml
# - Update organization and backend configuration

# 4. Add to version control and validate
cd iac-int-deploy
git add zones/zone-003/
git commit -m "Add zone-003 (eu-central-1) bootstrap configuration"
strata validate zones/zone-003/bootstrap/bootstrap-deploy.yaml --deep

# 5. Deploy zone (after landscape bootstrap is complete)
strata deploy run -f zones/zone-003/bootstrap/bootstrap-deploy.yaml --force

# 6. Verify zone is ready
strata env output -f zones/zone-003/bootstrap/bootstrap-deploy.yaml
```

### Onboarding a New Customer

```bash
# 1. Create customer structure from template
cd iac-int-deploy
mkdir -p customers/customer-xyz/{config,dev/config,dev/deploy,staging/config,staging/deploy,production/config,production/deploy}
cp -r customers/customer-template/* customers/customer-xyz/

# 2. Customize customer configuration
# Edit customers/customer-xyz/config/customer-config.yaml
# - Set customer_id and name
# - Set customer_namespace
# - Configure resource quotas

# Edit customers/customer-xyz/config/rbac.yaml
# - Define customer admins and developers
# - Set role bindings

# Edit customers/customer-xyz/config/secrets.yaml (ENCRYPTED)
# - Add customer API keys
# - Configure external service credentials

# 3. Customize environment configurations
# For each environment (dev, staging, production):
# - Edit customers/customer-xyz/{env}/config/environment.yaml
# - Edit customers/customer-xyz/{env}/config/database.yaml
# - Edit customers/customer-xyz/{env}/config/apps-values.yaml

# 4. Add to version control and validate
git add customers/customer-xyz/
git commit -m "Onboard customer-xyz with dev/staging/prod environments"
strata validate customers/customer-xyz/config/bootstrap.yaml --deep

# 5. Bootstrap customer (after zone bootstrap is complete)
strata deploy run -f customers/customer-xyz/config/bootstrap.yaml --force

# 6. Deploy customer environments (dev → staging → prod)
strata deploy run -f customers/customer-xyz/dev/deploy/deploy-dev.yaml --force
strata deploy run -f customers/customer-xyz/staging/deploy/deploy-staging.yaml --force
strata deploy run -f customers/customer-xyz/production/deploy/deploy-production.yaml --force
```

### Deploying a New Application to Customer Environment

```bash
# 1. Update application configuration
cd iac-int-deploy
# Edit customers/customer-xyz/production/config/apps-values.yaml
# - Add new service definition
# - Update image tags
# - Configure resource requests/limits

git add customers/customer-xyz/production/
git commit -m "Add new service to customer-xyz production"

# 2. Test in dev first (GitOps workflow)
strata validate customers/customer-xyz/dev/deploy/deploy-dev.yaml --deep
strata deploy run -f customers/customer-xyz/dev/deploy/deploy-dev.yaml --dry-run
strata deploy run -f customers/customer-xyz/dev/deploy/deploy-dev.yaml --force

# 3. Promote to staging after validation
strata deploy run -f customers/customer-xyz/staging/deploy/deploy-staging.yaml --dry-run
strata deploy run -f customers/customer-xyz/staging/deploy/deploy-staging.yaml --force

# 4. Deploy to production after staging soak tests
# (Review ArgoCD for continuous drift detection)
strata deploy run -f customers/customer-xyz/production/deploy/deploy-production.yaml --dry-run
strata deploy run -f customers/customer-xyz/production/deploy/deploy-production.yaml --force
```

### Updating Global Policies

```bash
# 1. Update landscape policies
cd iac-int-deploy
# Edit landscape/bootstrap/policies/global-policies.yaml
# - Add new compliance requirement
# - Update audit logging rules
# - Enhance security policies

git add landscape/bootstrap/
git commit -m "Enhance compliance policies for SOC2 audit"

# 2. Validate impact (test on dev landscape if available)
strata validate landscape/bootstrap/bootstrap-deploy.yaml --deep

# 3. Re-deploy landscape (safe if idempotent)
strata deploy run -f landscape/bootstrap/bootstrap-deploy.yaml --dry-run
strata deploy run -f landscape/bootstrap/bootstrap-deploy.yaml --force

# 4. Policies automatically cascade to all zones and customers
# Verify compliance across all tiers
strata audit list --level ERROR --output json
```

---

## Troubleshooting Bootstrap Issues

### Bootstrap Deployment Failed

```bash
# 1. Check audit log for details
strata audit list --last --output json

# 2. Identify which stage failed
strata audit list --level ERROR --output json | jq '.[] | {stage, error}'

# 3. Validate the failing stage's configuration
strata validate landscape/bootstrap/bootstrap-deploy.yaml --deep

# 4. Check external dependencies
strata tools status --output json

# 5. Retry bootstrap (safe if idempotent)
strata deploy run -f landscape/bootstrap/bootstrap-deploy.yaml --force
```

### Zone Not Connecting to Landscape

```bash
# 1. Verify landscape bootstrap completed
strata env output -f landscape/bootstrap/bootstrap-deploy.yaml | jq '.transit_gateway_id'

# 2. Check peering configuration
strata validate zones/zone-001/bootstrap/bootstrap-deploy.yaml --deep

# 3. Verify network configuration
# Edit zones/zone-001/bootstrap/zone-config.yaml
# Ensure peering_cidr matches landscape VPC

# 4. Re-run zone bootstrap
strata deploy run -f zones/zone-001/bootstrap/bootstrap-deploy.yaml --force

# 5. Verify connectivity
# Check cloud provider console for active peering connections
```

### Customer Cannot Access Resources

```bash
# 1. Verify customer bootstrap completed
strata env output -f customers/customer-xyz/config/bootstrap.yaml | jq '.namespace_id'

# 2. Check RBAC configuration
kubectl get rolebindings -n customer-xyz-namespace --output yaml

# 3. Verify customer environment deployment
strata env output -f customers/customer-xyz/production/deploy/deploy-production.yaml

# 4. Check pod logs in customer namespace
kubectl logs -n customer-xyz-namespace <pod-name> --tail 100
```

1. **Hierarchical Bootstrap**: Four-level bootstrap model (Landscape → Zone → Customer → Environment)
2. **Unified Configuration**: All orchestration and strata configs in `iac-int-deploy` (single source of truth)
3. **Specialized Deploy Repos**: Separate repos for Terraform, Ansible, Helm, and Logging (clear ownership)
4. **Tooling Separation**: Each team (Infrastructure, DevOps, Kubernetes, Observability) owns their own repo
5. **Multi-Tenancy**: Built-in tenant isolation at customer bootstrap level (Level 3)
6. **Regional Scalability**: Zone bootstrap enables multi-region deployments with central governance
7. **Auditability**: All changes tracked, versioned, and reversible across all repos and bootstrap levels
8. **Audit Sink**: `log-int-deploy` captures ALL deployment manifests and audit data — immutable, searchable record
9. **Compliance First**: Policies enforced at landscape level and inherited by all zones/customers/environments
10. **Progressive Deployment**: Changes promoted through dev → staging → production at environment level
11. **Infrastructure as Code**: All infrastructure, configs, and workloads defined in version control
12. **Reusability**: Shared Terraform modules in `tfmod-int-shared` across all levels and deployments
13. **GitOps Ready**: ArgoCD integration in `chart-int-deploy` for continuous reconciliation
14. **Observability Built-in**: Logging, auditing, and monitoring infrastructure via `log-int-deploy`
15. **Clear Boundaries**: Central config orchestrates; deploy repos focus on their domain
16. **Bootstrap Idempotency**: All bootstrap deployments must be idempotent and safe to re-run
17. **Strict Ordering**: Deployment levels must follow strict dependency order (no circular references)
18. **Deployment History**: Every deployment manifest versioned and archived for compliance and rollback

## Deployment Architecture Diagram

```
iac-int-deploy (Central Configuration & Orchestration)
├─ Level 1: Landscape Bootstrap (One-time global foundation)
│  ├─ landscape/bootstrap/ ─→ Global DNS, Secrets Vault, Monitoring
│  ├─ landscape/environments/ ─→ Global network config
│  └─ References → tf-int-main, chart-int-deploy
│
├─ Level 2: Zone Bootstrap (One per region)
│  ├─ zones/zone-001/bootstrap/ ─→ Regional VPC, Cluster, Peering
│  ├─ zones/zone-001/environments/ ─→ Zone-level configs
│  ├─ zones/zone-002/bootstrap/ ─→ (repeat for each region)
│  └─ References → tf-int-main, tfmod-int-shared, ans-int-config
│
├─ Level 3: Customer Bootstrap (One per tenant)
│  ├─ customers/001/config/ ─→ Namespace, RBAC, Secrets
│  ├─ customers/002/config/ ─→ (repeat for each customer)
│  └─ References → tfmod-int-shared
│
└─ Level 4: Customer Environment Deploy (Per environment)
   ├─ customers/001/dev/deploy/ ─→ App deployment (dev)
   ├─ customers/001/staging/deploy/ ─→ App deployment (staging)
   ├─ customers/001/production/deploy/ ─→ App deployment (prod)
   └─ References → tf-int-main, tfmod-int-shared, chart-int-deploy, log-int-deploy
```

**External Deploy Repositories:**
```
tf-int-main ←─ references ←─ iac-int-deploy
  ├─ terraform/main.tf
  ├─ networking/, compute/, storage/
  └─ uses modules from ↓

tfmod-int-shared (modules library)
  ├─ modules/networking/
  ├─ modules/compute/
  ├─ modules/database/
  └─ used by all levels

ans-int-config (configuration automation)
  ├─ playbooks/
  ├─ roles/
  └─ post-provisioning tasks

chart-int-deploy (K8s workloads & GitOps)
  ├─ helm/charts/
  ├─ argocd/
  └─ application deployments

log-int-deploy (logging infrastructure)
  ├─ terraform/elasticsearch/
  ├─ terraform/audit-trail/
  └─ observability stack
```

## Repository Responsibilities by Bootstrap Level

| Responsibility                    | Landscape | Zone | Customer | Environment | Responsible Repo |
| --------------------------------- | --------- | ---- | -------- | ----------- | ---------------- |
| **Global DNS & Secrets**          | ✓         |      |          |             | tf-int-main      |
| **Transit Gateway**               | ✓         |      |          |             | tfmod-int-shared |
| **Global Monitoring**             | ✓         |      |          |             | chart-int-deploy |
| **Regional VPC**                  |           | ✓    |          |             | tfmod-int-shared |
| **Regional Cluster**              |           | ✓    |          |             | tfmod-int-shared |
| **Zone-to-Landscape Peering**     |           | ✓    |          |             | tfmod-int-shared |
| **Zone Platform Services**        |           | ✓    |          |             | chart-int-deploy |
| **Customer Namespace**            |           |      | ✓        |             | tfmod-int-shared |
| **Customer RBAC**                 |           |      | ✓        |             | tfmod-int-shared |
| **Customer Secrets**              |           |      | ✓        |             | iac-int-deploy   |
| **Customer Compliance Policies**  |           |      | ✓        |             | iac-int-deploy   |
| **Application Databases**         |           |      |          | ✓           | tfmod-int-shared |
| **Application Deployments**       |           |      |          | ✓           | chart-int-deploy |
| **Environment Monitoring**        |           |      |          | ✓           | chart-int-deploy |
| **Environment Backup**            |           |      |          | ✓           | tfmod-int-shared |
| **Server Provisioning**           |           |      | ✓        |             | ans-int-config   |
| **Security Hardening**            |           |      | ✓        |             | ans-int-config   |
| **Logging Infrastructure**        | ✓         | ✓    | ✓        | ✓           | log-int-deploy   |
| **Deployment Manifest Archiving** | ✓         | ✓    | ✓        | ✓           | log-int-deploy   |
| **Audit & Compliance Records**    | ✓         | ✓    | ✓        | ✓           | log-int-deploy   |

## Development Teams & Ownership

| Team                       | Primary Responsibility                        | Bootstrap Level | Repositories                                  |
| -------------------------- | --------------------------------------------- | --------------- | --------------------------------------------- |
| **Platform Team**          | Global governance, landscape bootstrap        | Level 1         | iac-int-deploy, tf-int-main                   |
| **Infrastructure Team**    | Regional infrastructure, modules, zones       | Levels 2-4      | tf-int-main, tfmod-int-shared, ans-int-config |
| **Kubernetes/DevOps Team** | Container orchestration, workloads            | Levels 2-4      | chart-int-deploy, ans-int-config              |
| **Customer Success Team**  | Customer onboarding, tenant setup             | Level 3         | iac-int-deploy                                |
| **Application Team**       | Environment deployments, apps                 | Level 4         | chart-int-deploy, iac-int-deploy              |
| **Observability Team**     | Logging, monitoring, audit trails, compliance | All Levels      | log-int-deploy, chart-int-deploy              |
| **Audit/Compliance Team**  | Audit records, deployment history, compliance | All Levels      | log-int-deploy                                |
