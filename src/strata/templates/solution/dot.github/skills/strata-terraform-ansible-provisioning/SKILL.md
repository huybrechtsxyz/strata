---
name: strata-terraform-ansible-provisioning
description: 'Strata provisioner integration: Terraform for IaC, Ansible for post-provisioning configuration, Helm/Compose, stage orchestration, and stage-scoped secrets. Use when configuring or debugging a deployment stage.'
---

# Strata Terraform and Ansible Provisioning

## Provisioners in strata

Provisioners are deployment execution engines. Strata supports:

| Provisioner      | Purpose                                          | When to Use                                      |
| ---------------- | ---------------------------------------------------- | ---------------------------------------------------- |
| `terraform`      | Infrastructure-as-Code (cloud resources)         | Provision clusters, networks, storage, VMs       |
| `ansible`        | Post-provisioning configuration (apps, services) | Configure servers, install packages, deploy apps |
| `helm`           | Kubernetes Helm charts                            | Deploy applications to Kubernetes                |
| `docker_compose` | Docker Compose stacks                             | Dev/test environments, multi-container apps      |

---

## Terraform Provisioner

Generates and applies Terraform code to provision cloud infrastructure.

### Configuration in strata

**In `configuration.yaml`:**

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: my-platform
spec:
  provisioners:
    - name: terraform
      type: terraform
      source:
        repository: haven
        source_path: terraform
      backend:
        type: terraform_cloud
        configuration:
          organization: myorg
          workspace: my-platform-prod
```

**In `deployment.yaml`:**

```yaml
spec:
  stages:
    - name: infrastructure
      provisioner: terraform
      scope: all                      # all resources or specific names
      on_failure: stop                # stop | rollback | continue
      secrets: [db_password]          # allowlist — only these keys reach this stage
      before_scripts: []              # optional: run before Terraform
      after_scripts: []               # optional: run after Terraform
```

### How Terraform Provisioning Works

1. **Strata generates Terraform code** from resource definitions
2. **Code is written to** `{build_path}/terraform/`
3. **Strata runs** `terraform init` (first time) and `terraform apply`
4. **State is stored** in Terraform Cloud / S3 / local backend
5. **Outputs captured** for next stages or deployment record

### Terraform State Management

**State backend configuration:**

```yaml
backend:
  type: terraform_cloud            # terraform_cloud, aws_s3, local, gcs
  configuration:
    organization: myorg
    workspace: my-platform-prod
    # For S3:
    # bucket: my-tf-state
    # key: prod/terraform.tfstate
    # region: us-east-1
```

**State locking:**
- Terraform Cloud automatically locks during apply (prevents concurrent changes)
- S3 backend uses DynamoDB for locking
- Local backend has no locking (use carefully in CI/CD)

**Check state:**

```bash
strata values get -f deploy.yaml terraform_state --output json
```

### Terraform Provider Variables

Environment variables injected during `terraform apply`:

| Variable            | Content                                                          |
| ---------------------- | -------------------------------------------------------------------- |
| `TF_VAR_*`          | Variables resolved for this deployment (prefixed `TF_VAR_`)      |
| `TF_WORKSPACE`      | Current Terraform workspace name                                  |
| `STRATA_PHASE`      | Current deployment stage                                           |
| `STRATA_BUILD_PATH` | Path to generated artifacts                                       |

Only variables/secrets in the stage's `secrets:` allowlist (see below) become `TF_VAR_*` for that stage's Terraform run.

### Common Terraform Patterns

**Limit to specific resources:**

```bash
# Deploy only networking infrastructure
strata deploy run -f deploy/prod.yaml --stage infrastructure --scope networking
```

**Destroy specific resources:**

```bash
# Destroy only temporary test resources
terraform destroy -target=aws_instance.test_vm -auto-approve
```

**Import existing infrastructure:**

```bash
# Bring existing Azure resource under Terraform management
terraform import azurerm_resource_group.existing /subscriptions/.../resourceGroups/my-rg
```

---

## Ansible Provisioner

Configures servers and applications after infrastructure is provisioned (post-provisioning).

### Configuration in strata

**In `configuration.yaml`:**

```yaml
provisioners:
  - name: ansible
    type: ansible
    source:
      repository: haven
      source_path: ansible
    configuration:
      playbook: site.yml
      inventory: inventory/hosts.yml
      ssh_private_key_secret: ssh_key_prod    # reference to secret
      extra_vars:
        env: production
```

**In `deployment.yaml`:**

```yaml
stages:
  - name: configuration
    provisioner: ansible
    scope: all
    on_failure: continue  # if this playbook fails, log and move to next stage
    secrets: [ssh_key_prod]  # must include the SSH key (and any other secret the playbook needs)
    before_scripts: []
    after_scripts: []
```

### SSH Key Pattern (Critical)

**NEVER hardcode SSH keys. Use secret references:**

```yaml
configuration:
  ssh_private_key_secret: ssh_key_prod
```

**In `environment.yaml`:**

```yaml
spec:
  secrets:
    - key: ssh_key_prod
      store: bitwarden
      value: ssh-key-item-123
      # Item contains full PEM-formatted private key
```

**Strata's SSH key lifecycle:**
1. Read key from secret store at deploy time
2. Write to temp file (chmod 600)
3. Pass to Ansible via `--private-key` flag
4. Delete temp file immediately after Ansible completes
5. **Key never appears in logs or artifact files**

**Agent rule:** the stage running this Ansible playbook must include `ssh_key_prod` in its `secrets:` allowlist — a value resolving at the environment level doesn't automatically flow to a stage that never asked for it.

### Ansible Inventory

Two patterns:

**Pattern 1: Static inventory file in repository**

```yaml
configuration:
  inventory: inventory/hosts.yml
```

File structure:

```yaml
# inventory/hosts.yml
all:
  children:
    webservers:
      hosts:
        web1:
          ansible_host: 10.0.1.10
        web2:
          ansible_host: 10.0.1.11
    databases:
      hosts:
        db1:
          ansible_host: 10.0.2.10
```

**Pattern 2: Dynamic inventory (generated by Terraform)**

```yaml
configuration:
  inventory: ""  # empty = use dynamic from Terraform outputs
  dynamic_inventory_script: generate_inventory.py
```

Terraform generates:

```json
{
  "webservers": {
    "hosts": ["10.0.1.10", "10.0.1.11"]
  }
}
```

### Ansible Extra Variables

Pass variables from strata to playbooks:

```yaml
configuration:
  extra_vars:
    environment: prod
    region: eastus
    app_version: 1.2.3
```

In playbook:

```yaml
- name: Deploy application
  hosts: webservers
  vars:
    app_version: "{{ app_version }}"  # injected by strata
  tasks:
    - name: Pull Docker image
      docker_image:
        name: myapp:{{ app_version }}
```

### Ansible Playbook Structure

**Recommended layout:**

```
ansible/
├── site.yml              # Main playbook (called by strata)
├── inventory/
│   ├── hosts.yml         # Static hosts
│   └── prod.yml          # Prod-specific inventory
├── roles/
│   ├── common/           # Common setup
│   ├── webserver/        # Web server role
│   ├── database/         # Database role
│   └── security/         # Security hardening
└── group_vars/
    ├── all.yml           # Variables for all hosts
    └── webservers.yml    # Variables for webserver group
```

**site.yml example:**

```yaml
---
- name: Configure infrastructure
  hosts: all
  gather_facts: yes
  pre_tasks:
    - name: Update package cache
      apt:
        update_cache: yes

- name: Configure webservers
  hosts: webservers
  roles:
    - common
    - webserver
    - security

- name: Configure databases
  hosts: databases
  roles:
    - common
    - database
```

---

## Multi-Stage Orchestration

Deploy infrastructure first (Terraform), then configure (Ansible):

```yaml
spec:
  stages:
    # Stage 1: Provision infrastructure
    - name: infrastructure
      provisioner: terraform
      scope: all
      on_failure: stop  # if Terraform fails, stop entirely
      secrets: [db_password]
      before_scripts: []
      after_scripts:
        - bash: "echo 'Infrastructure provisioned'"

    # Stage 2: Configure servers (waits for Stage 1 to complete)
    - name: configuration
      provisioner: ansible
      scope: all
      on_failure: continue  # if Ansible fails, log but continue
      secrets: [ssh_key_prod]
      before_scripts:
        - bash: "echo 'Starting configuration...'"
      after_scripts: []
```

**Execution flow:**

```
Terraform runs → creates infrastructure, outputs IPs
↓
Ansible inventories the new servers (from Terraform outputs)
↓
Ansible configures applications and services
↓
Post-scripts run (health checks, verification)
```

### Output Passing Between Stages

**Terraform outputs:**

```hcl
# terraform/outputs.tf
output "webserver_ips" {
  value = [for server in azurerm_linux_virtual_machine.web : server.private_ip_address]
}

output "database_endpoint" {
  value = azurerm_mysql_server.db.fqdn
}
```

**Ansible uses Terraform outputs:**

```yaml
# Extra vars injection
extra_vars:
  database_endpoint: "{{ terraform_outputs.database_endpoint }}"
  webserver_ips: "{{ terraform_outputs.webserver_ips }}"
```

**In playbook:**

```yaml
- name: Configure database connection
  lineinfile:
    path: /etc/app.conf
    regexp: "^DATABASE_HOST="
    line: "DATABASE_HOST={{ database_endpoint }}"
```

---

## Error Handling in Stages

### on_failure: stop (default)

Halts entire deployment if stage fails:

```yaml
- name: infrastructure
  provisioner: terraform
  on_failure: stop  # next stage won't run if this fails
```

**Use for:** Critical stages (infrastructure provisioning, security setup)

### on_failure: rollback

Same halt behavior as `stop` — the deploy aborts and subsequent stages don't run. Use this value where you want to explicitly signal intent to roll back (strata itself does not auto-rollback; see the `strata-deployment-lifecycle` skill for manual rollback patterns).

### on_failure: continue

Logs error but continues to next stage:

```yaml
- name: monitoring
  provisioner: ansible
  on_failure: continue  # even if monitoring setup fails, continue to next
```

**Use for:** Optional stages (monitoring, logging, alerting)

### Lifecycle Scripts for Error Recovery

```yaml
stages:
  - name: infrastructure
    provisioner: terraform
    after_scripts:
      - bash: |
          if [ $? -ne 0 ]; then
            echo "Terraform failed, checking state..."
            terraform show
          fi
```

---

## Provisioner-Specific Environment Variables

Available in provisioner configs:

| Variable                | Content                                              |
| --------------------------- | -------------------------------------------------------- |
| `STRATA_PHASE`          | Stage name (e.g., `infrastructure`, `configuration`) |
| `STRATA_WORKSPACE_PATH` | Path to workspace root                               |
| `STRATA_BUILD_PATH`     | Path to generated artifacts                          |
| `STRATA_OBJECT_PATH`    | Path to built objects                                 |
| `TF_*`                  | Terraform variables (for Terraform provisioner)      |
| `ANSIBLE_*`             | Ansible variables (for Ansible provisioner)          |

---

## Best Practices

### Terraform

1. **Use remote state** (Terraform Cloud / S3) — enables team collaboration
2. **Enable state locking** — prevents concurrent changes
3. **Pin provider versions** — reproducible deploys
4. **Use outputs** — pass values to next stages
5. **Separate per environment** — different workspaces
6. **Validate before apply** — `terraform plan` first

### Ansible

1. **Use roles** — organize playbooks into reusable units
2. **Store credentials in secret store** — never in playbooks
3. **Use group_vars** — environment-specific variables
4. **Idempotent tasks** — can run multiple times safely
5. **Test in dev first** — validate before prod deploy
6. **Use handler notifications** — restart services only when needed

### General

1. **Separate infrastructure and configuration** — different stages
2. **Fail fast** — use `on_failure: stop` for critical stages
3. **Document stages** — clear purpose for each stage
4. **Declare `secrets:` explicitly on every stage** — don't assume a resolved value reaches the provisioner
5. **Use lifecycle scripts** — pre/post stage actions
6. **Monitor deployment** — health checks after each stage
7. **Keep stages atomic** — independently retryable
8. **Log everything** — audit trail for debugging

---

## Troubleshooting

| Problem                     | Cause                                    | Fix                                                        |
| -------------------------------- | ---------------------------------------------- | ---------------------------------------------------------------- |
| Terraform state locked      | Previous deploy didn't complete           | Check `strata deploy status`, manually unlock if stuck    |
| SSH key permission denied   | Key not 600 permissions                    | Verify SSH key in secret store is valid PEM                |
| Ansible unreachable          | Hosts not in inventory or SSH failed       | Check inventory config, SSH key, network connectivity      |
| Terraform drift              | Infrastructure changed outside strata      | Run `strata build plan` to detect, re-apply or import      |
| Ansible idempotency          | Task runs every time (not idempotent)      | Use Ansible modules (apt, yum, copy) instead of shell       |
| "No value for required variable" | Value resolved but stage's `secrets:` allowlist omits it | Add the key to that stage's `secrets:` list       |
