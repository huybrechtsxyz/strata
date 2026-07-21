---
name: "strata-terraform-ansible-provisioning"
description: "Terraform and Ansible provisioner configuration, stage orchestration, and post-provisioning patterns"
domain: "provisioning"
confidence: "high"
source: "strata.instructions.md, ADR-0005"
tools:
  - name: "strata deploy run"
    description: "Execute provisioning stages"
    when: "apply infrastructure and configuration changes"
  - name: "terraform"
    description: "Infrastructure provisioning (managed by strata)"
    when: "provisioning stage runs Terraform"
  - name: "ansible-playbook"
    description: "Server configuration (managed by strata)"
    when: "configuration stage runs Ansible"
---

## Context

Strata supports two provisioner types: **Terraform** (for infrastructure) and **Ansible** (for post-provision configuration). Deployment stages chain these provisioners in sequence. Understanding how each works, how they integrate, and how to handle failures is essential for reliable deployments.

## Terraform Provisioner

### Purpose
Generate and apply Terraform to provision cloud infrastructure (VMs, networks, databases, load balancers, etc.).

### Configuration

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: Deployment
meta:
  name: deploy-prod
spec:
  stages:
    - name: infrastructure
      provisioner: terraform
      scope: all
      on_failure: stop          # stop: halt on error | continue: keep going
```

### How It Works

1. **strata build run** generates Terraform HCL from platform manifest
2. **strata deploy run** (terraform stage):
   - Initializes Terraform backend (connects to state storage)
   - Runs `terraform plan` (shows what will change)
   - User approves or rejects
   - Runs `terraform apply` (applies changes)
   - Records state in backend

### Backend Configuration

```yaml
spec:
  provisioners:
    - name: infra
      provisioner: terraform
      source:
        repository: haven
        source_path: terraform
      backend:
        type: terraform_cloud        # or terraform_local, terraform_s3, terraform_azurerm
        configuration:
          organization: myorg
          workspace: haven-prd
```

### Terraform Provider Integration

```yaml
spec:
  providers:
    - name: azure
      region: eastus
      sku: standard

  resources:
    - kind: azure_resource_group
      name: prod-rg
      provider: azure
```

**Strata generates:**
```hcl
terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
}

resource "azurerm_resource_group" "prod_rg" {
  name     = "prod-rg"
  location = var.azure_region
}
```

### Variable Injection

Pass values from strata to Terraform:

```yaml
spec:
  secrets:
    - key: subscription_id
      source: azure_keyvault
      value: "subscription-id"

  terraform:
    variables:
      - name: subscription_id
        value: "${secrets.subscription_id}"
      - name: environment
        value: "production"
```

### Failure Handling

| Failure | Signal | Recovery |
|---------|--------|----------|
| **Plan fails** | Terraform syntax error | Fix HCL, rebuild, retry |
| **Apply fails** | Resource creation error (permissions, quota, etc.) | Fix configuration, `terraform destroy` if partial, retry |
| **State lock held** | "Another operation in progress" | Wait for previous deploy or manually unlock (dangerous) |
| **Backend unreachable** | "Cannot connect to state" | Check backend config, credentials, network |

### Recovery Pattern

```bash
# 1. Check what happened
strata audit list --last --output json

# 2. Review Terraform state
terraform -chdir=.strata/build/terraform state list

# 3. Destroy partial resources (if needed)
terraform -chdir=.strata/build/terraform destroy -auto-approve

# 4. Fix configuration
# (edit deployment files)

# 5. Retry
strata build run -f deploy.yaml --output json
strata deploy run -f deploy.yaml --dry-run --output json
strata deploy run -f deploy.yaml --force --output json
```

## Ansible Provisioner

### Purpose
Configure provisioned servers after Terraform creates them (install software, configure services, deploy applications).

### Configuration

```yaml
spec:
  stages:
    - name: configuration
      provisioner: ansible
      scope: all
      configuration:
        playbook: site.yml                    # playbook file in repository
        inventory: inventory/hosts.yml        # inventory file (auto-discovered if omitted)
        ssh_private_key_secret: haven_ssh_key # secret key reference (see Secret Resolution)
        extra_vars:
          env: production
          debug: false
```

### Inventory

Ansible needs a list of hosts to configure. Strata provides two patterns:

**Pattern 1: Static inventory file**
```yaml
configuration:
  playbook: site.yml
  inventory: inventory/prod.yml
```

**hosts.yml example:**
```yaml
all:
  children:
    webservers:
      hosts:
        web01:
          ansible_host: 10.0.1.10
        web02:
          ansible_host: 10.0.1.11
    databases:
      hosts:
        db01:
          ansible_host: 10.0.2.10
```

**Pattern 2: Dynamic inventory from Terraform**
```yaml
configuration:
  playbook: site.yml
  inventory_source: terraform
  # strata auto-generates inventory from Terraform outputs
```

### SSH Key Management

Ansible requires SSH access. Strata handles SSH keys securely:

```yaml
spec:
  secrets:
    - key: haven_ssh_key
      source: bitwarden
      value: "<item-id>"  # contains full PEM key

  stages:
    - name: configuration
      provisioner: ansible
      configuration:
        playbook: site.yml
        ssh_private_key_secret: haven_ssh_key
```

**What happens:**
1. At deploy time, strata fetches PEM key from Bitwarden
2. Writes to temp file with `chmod 600` (read-only)
3. Ansible uses temp file for SSH
4. After Ansible completes, temp file is deleted
5. Key never persisted to disk, never committed to git

### Playbook Example

```yaml
---
- name: Configure web servers
  hosts: webservers
  become: yes
  tasks:
    - name: Install packages
      apt:
        name:
          - nginx
          - curl
          - git
        state: present

    - name: Start nginx
      systemd:
        name: nginx
        state: started
        enabled: yes

    - name: Deploy application
      git:
        repo: "{{ app_repo }}"
        dest: /opt/app
        version: "{{ app_version }}"
```

### Extra Variables

Pass values from strata to Ansible:

```yaml
configuration:
  playbook: site.yml
  extra_vars:
    environment: production
    app_version: "1.2.3"
    enable_monitoring: true
```

**Accessed in playbook:**
```yaml
- name: Set environment
  set_fact:
    ENV_NAME: "{{ environment }}"
    APP_VER: "{{ app_version }}"
```

### Failure Handling

| Failure | Signal | Recovery |
|---------|--------|----------|
| **SSH auth fails** | "Permission denied" | Verify SSH key, check public key in authorized_keys |
| **Host unreachable** | "Could not connect" | Check host IP, network connectivity, security groups |
| **Task fails** | Ansible error in playbook | Fix playbook, retry (Terraform skips if already applied) |
| **Timeout** | Task hangs | Increase timeout, debug playbook locally first |

### Recovery Pattern

```bash
# 1. Check last execution
strata audit list --last --output json

# 2. Review Ansible logs
cat .strata/build/ansible-run.log

# 3. Test playbook locally (if possible)
ansible-playbook site.yml -i inventory/test.yml --check

# 4. Fix playbook
# (edit site.yml)

# 5. Retry configuration stage only (skip infrastructure)
strata deploy run -f deploy.yaml --stage configuration --force --output json
```

## Multi-Stage Deployment

Stages execute sequentially. Typical pattern:

```yaml
spec:
  stages:
    - name: infrastructure
      provisioner: terraform
      scope: all
      on_failure: stop

    - name: configuration
      provisioner: ansible
      scope: all
      on_failure: stop
```

### Stage-Specific Deploy

```bash
# Deploy infrastructure only
strata deploy run -f deploy.yaml --stage infrastructure --force --output json

# Verify infrastructure
# (manual checks or health script)

# Deploy configuration only (Terraform skipped)
strata deploy run -f deploy.yaml --stage configuration --force --output json
```

### Failure Isolation

```yaml
# Continue even if a stage fails (for partial rollout)
stages:
  - name: infrastructure
    provisioner: terraform
    on_failure: continue       # keep going even if Terraform fails

  - name: configuration
    provisioner: ansible
    on_failure: stop           # stop on first Ansible error
```

## Common Patterns

### 1. Multi-Region Deployment

```yaml
spec:
  providers:
    - name: azure_east
      region: eastus
    - name: azure_west
      region: westus

  resources:
    - name: prod-rg-east
      provider: azure_east
    - name: prod-rg-west
      provider: azure_west

  stages:
    - name: infrastructure
      provisioner: terraform
      scope: all
```

**Terraform generates:**
```hcl
provider "azurerm" {
  alias = "azure_east"
  region = "eastus"
}

provider "azurerm" {
  alias = "azure_west"
  region = "westus"
}

resource "azurerm_resource_group" "prod_rg_east" {
  provider = azurerm.azure_east
  ...
}
```

### 2. Conditional Provisioning

```yaml
spec:
  stages:
    - name: infrastructure
      provisioner: terraform
      scope: all

    - name: configuration
      provisioner: ansible
      scope: webservers    # only configure webservers group
```

### 3. Pre/Post Hooks

```yaml
configuration:
  playbook: site.yml
  pre_tasks:
    - name: Backup current config
      shell: tar -czf /tmp/backup.tar.gz /etc/app/
  
  post_tasks:
    - name: Notify team
      mail:
        host: smtp.example.com
        subject: "Deployment complete"
```

## Troubleshooting Workflow

```bash
# 1. Validate before anything
strata validate deploy.yaml --deep --output json

# 2. Build and review artifacts
strata build run -f deploy.yaml --output json
cat .strata/build/terraform/main.tf
cat .strata/build/ansible/site.yml

# 3. Dry-run
strata deploy run -f deploy.yaml --dry-run --output json

# 4. Review what Terraform will change
terraform -chdir=.strata/build/terraform plan

# 5. Review Ansible playbook
ansible-playbook .strata/build/ansible/site.yml --check

# 6. Deploy
strata deploy run -f deploy.yaml --force --output json

# 7. Verify
strata deploy status -f deploy.yaml --output json
strata deploy health -f deploy.yaml --output json
```

## Agent Responsibilities

- **Always validate deployment YAML before provisioning**
- **Use `--dry-run` before actual deploy**
- **Review Terraform plan output** before approving
- **Test Ansible locally** before deploying to production
- **Store SSH keys in secret store, never in git**
- **Check Terraform state** for consistency
- **Monitor stage-specific failures** and recover appropriately
- **Use `on_failure: continue`** only when partial rollout is acceptable
