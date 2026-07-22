---
name: "strata-deployment-lifecycle"
description: "Complete validate → build → deploy → audit workflow with stages, provisioners, and state management"
domain: "deployment-operations"
confidence: "high"
source: "strata.instructions.md, ADRs 0005-0008"
tools:
  - name: "strata validate"
    description: "Structural and policy validation"
    when: "before every build/deploy"
  - name: "strata build"
    description: "Generate artifacts and dry-run"
    when: "prepare deployment without touching cloud resources"
  - name: "strata deploy"
    description: "Execute provisioning and configuration"
    when: "apply changes to live infrastructure"
  - name: "strata audit"
    description: "Review deployment history and logs"
    when: "troubleshoot failures or verify what was deployed"
---

## Context

The deployment lifecycle moves through **four distinct phases**: validate, build, deploy, audit. Each phase has specific responsibilities, failure modes, and recovery patterns. Understanding this lifecycle ensures agents coordinate deployments safely and predictably.

## Phase 1: Validate

**Goal:** Catch structural and policy errors BEFORE building artifacts.

```bash
# Quick structural validation
strata validate deploy/deploy-prd.yaml --output json

# Deep validation (cross-refs, policy, requires active profile)
strata validate deploy/deploy-prd.yaml --deep --output json
```

### What Happens

1. **Schema check:** YAML structure, required fields, type validation
2. **Cross-reference resolution:** `@repo_name/path.yaml` references resolve correctly
3. **Policy check (deep only):** Security, compliance, deployment guardrails
4. **Profile binding (deep only):** Variables, secrets, environment overrides apply

### Success Condition
- Exit code 0
- No errors in `errors` array

### Failure Handling
- **Exit 3 (validation error):** Read `errors` array, fix YAML, retry
- **Exit 1 (system error):** Check profile is active (`strata profile activate <name>`), check repos added
- **Never proceed to build if validation fails**

### Agent Pattern

```bash
# ALWAYS validate first
strata validate <file> --deep --output json
if [ $? -eq 0 ]; then
  # success — proceed to build
else
  # failure — fix and retry
fi
```

## Phase 2: Build

**Goal:** Generate Terraform and platform artifacts WITHOUT modifying cloud resources.

```bash
# Full build (generates all artifacts)
strata build run -f deploy/deploy-prd.yaml --output json

# Dry-run (validate + plan without writing)
strata build run -f deploy/deploy-prd.yaml --dry-run --output json

# Plan only (show what changed vs last build)
strata build plan -f deploy/deploy-prd.yaml --output json

# Generate SBOM
strata build sbom -f deploy/deploy-prd.yaml --output json

# Clean previous artifacts
strata build clean -f deploy/deploy-prd.yaml --output json
```

### Build Artifacts

```
.strata/
├── build/
│   ├── platform.json          # Platform manifest (all resources)
│   ├── terraform/             # Generated Terraform code
│   │   ├── main.tf
│   │   ├── variables.tf
│   │   └── outputs.tf
│   ├── ansible/               # Generated Ansible playbooks
│   └── sbom.json              # Supply chain manifest
└── ...
```

### What Happens

1. **Resolve remotes:** Fetch all configuration from registered repositories
2. **Merge templates:** Combine workspace, module, provider, environment specs
3. **Generate platform.json:** Unified manifest of all resolved resources
4. **Generate Terraform:** Translate platform manifest to HCL
5. **Generate Ansible:** Translate provisioning specs to playbooks
6. **Build summary:** List generated files, counts, and warnings

### Success Condition
- Exit code 0
- Build artifacts created in `.strata/build/`
- `data.artifacts` in JSON output lists all generated files

### Failure Handling
- **Exit 3 (validation):** Fix YAML, clean artifacts, retry build
- **Exit 1 (system):** Check file permissions, disk space, network connectivity
- **Deploy dry-run to verify:** Before committing, run `strata deploy run --dry-run`

### Dry-Run Pattern

```bash
# Plan without committing
strata build run -f deploy/deploy-prd.yaml --dry-run --output json

# Review generated artifacts (won't be written)
# Then run full build
strata build run -f deploy/deploy-prd.yaml --output json
```

## Phase 3: Deploy

**Goal:** Execute infrastructure provisioning and configuration in stages, with rollback capability.

```bash
# ALWAYS dry-run first
strata deploy run -f deploy/deploy-prd.yaml --dry-run --output json

# Execute deployment (requires --force for automation)
strata deploy run -f deploy/deploy-prd.yaml --force --output json

# Deploy specific stage only
strata deploy run -f deploy/deploy-prd.yaml --stage infrastructure --force --output json
strata deploy run -f deploy/deploy-prd.yaml --stage configuration --force --output json

# Check deployment status
strata deploy status -f deploy/deploy-prd.yaml --output json

# View deployment history
strata deploy history -f deploy/deploy-prd.yaml --output json

# Health check
strata deploy health -f deploy/deploy-prd.yaml --output json
```

### Deployment Stages

Stages execute sequentially. Each stage can use either **Terraform** (infrastructure) or **Ansible** (configuration):

```yaml
stages:
  - name: infrastructure
    provisioner: terraform
    scope: all
    on_failure: stop   # stop: halt on error | continue: keep going

  - name: configuration
    provisioner: ansible
    scope: all
    configuration:
      playbook: site.yml
      ssh_private_key_secret: ssh_key
```

**Terraform provisioner:**
- Initializes Terraform backend
- Plans changes
- Applies if approved
- Manages state lockfile (prevents concurrent deploys)

**Ansible provisioner:**
- SSH into provisioned hosts
- Runs playbook
- Collects results

### What Happens During Deploy

1. **Pre-deploy checks:** State lock acquired, profiles validated
2. **Stage 1 (infrastructure):** Terraform provisions resources
3. **Stage 2+ (configuration):** Ansible configures provisioned resources
4. **Deployment locked:** Only one deployment can run at a time per workspace
5. **Success/rollback:** Logs recorded, state persisted

### Failure Handling

| Failure | Signal | Recovery |
|---------|--------|----------|
| **Terraform plan fails** | Exit 1 during stage | Fix HCL, clean, rebuild, retry deploy |
| **Ansible task fails** | Exit 1 during stage | Fix playbook, retry deploy (Terraform skips if already applied) |
| **State lock held** | "Cannot acquire lock" error | Previous deploy in progress — wait or `strata deploy status` to check |
| **Rollback needed** | Manual intervention | `strata deploy destroy` to tear down, then retry |

### Workflow Pattern

```bash
# 1. Validate
strata validate deploy/deploy-prd.yaml --deep --output json

# 2. Build
strata build run -f deploy/deploy-prd.yaml --output json

# 3. Dry-run
strata deploy run -f deploy/deploy-prd.yaml --dry-run --output json

# 4. Review output
# 5. Deploy
strata deploy run -f deploy/deploy-prd.yaml --force --output json

# 6. Verify
strata deploy status -f deploy/deploy-prd.yaml --output json
strata deploy health -f deploy/deploy-prd.yaml --output json
```

## Phase 4: Audit

**Goal:** Review what happened, troubleshoot failures, maintain compliance trail.

```bash
# Last execution only
strata audit list --last --output json

# Filter by level
strata audit list --level ERROR --output json
strata audit list --level WARN --output json

# Last N minutes
strata audit list --minutes 10 --output json

# Full history
strata audit list --output json
```

### Audit Entry Contents

- **timestamp:** when the operation ran
- **operation:** `build`, `deploy`, `destroy`, `validate`
- **status:** `success`, `failure`, `partial`
- **stage:** which stage (for multi-stage deploys)
- **duration:** how long it took
- **error_message:** if failed, why
- **execution_id:** unique ID for correlating logs

### Troubleshooting Pattern

```bash
# 1. Get last execution
strata audit list --last --output json

# 2. Review errors
strata audit list --level ERROR --output json

# 3. Check all recent activity
strata audit list --minutes 30 --output json

# 4. Correlate with deployment
strata deploy history -f deploy/deploy-prd.yaml --output json
```

## State Management

### State Lockfile

- **Location:** `.strata/state.lock`
- **Duration:** Acquired during deploy, released after completion
- **Purpose:** Prevents concurrent deployments from conflicting
- **Timeout:** 30 minutes (if deploy hangs, lock auto-releases)

### State Lock Failures

```bash
# Check if locked
strata deploy status -f deploy/deploy-prd.yaml --output json

# If locked by older deployment:
# Option 1: Wait for timeout (30 min)
# Option 2: Manually release (if you KNOW the deploy is not running):
#   rm .strata/state.lock
# Option 3: Check audit to see what was deploying
#   strata audit list --output json
```

## Multi-Stage Deployment Pattern

```bash
# Deploy infrastructure only (databases, networks, VMs)
strata deploy run -f deploy/deploy-prd.yaml --stage infrastructure --force --output json

# Verify infrastructure deployed
strata deploy health -f deploy/deploy-prd.yaml --output json

# Deploy configuration (app config, SSL certs, monitoring)
strata deploy run -f deploy/deploy-prd.yaml --stage configuration --force --output json

# Full verification
strata deploy status -f deploy/deploy-prd.yaml --output json
```

## Rollback Pattern

When deployment must be rolled back:

```bash
# 1. Check what was deployed
strata deploy history -f deploy/deploy-prd.yaml --output json

# 2. Destroy (manual process — no auto-rollback yet)
strata deploy destroy -f deploy/deploy-prd.yaml --force --output json

# 3. Fix YAML or configuration
# (edit deployment files)

# 4. Re-deploy
strata validate <file> --deep --output json
strata build run -f <file> --output json
strata deploy run -f <file> --force --output json
```

## Agent Responsibilities

- **Always validate before build**
- **Always build before deploy**
- **Always dry-run before actual deploy**
- **Never skip phases** to save time
- **Check state lock** before force-deploying
- **Audit after failure** to understand what broke
- **Use `--force` only in automation** (skips confirmation prompts)
- **Use stage-specific deploy** when not all stages need to run
