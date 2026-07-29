# Deployment Manifests Guide

> 📐 **Design rationale:** [ADR-0021 — Deployment manifests as first-class build artifacts](../decisions/0021-deployment-manifests-as-first-class-build-artifacts.md)

Learn how to enable, interpret, and use deployment manifests for compliance, auditing, and operational visibility.

---

## Overview

**Deployment manifests** are immutable snapshots created at two points in your workflow:

1. **Build time** — `strata build run` captures the configuration and build outputs (artifacts, SBOM)
2. **Deploy time** — `strata deploy run` captures the full deployment record (provisioner outputs, stage results)

Think of manifests as **evidence** — a complete record of what was intended (build) and what actually happened (deploy). Together, they form your compliance audit trail.

---

## What is a Deployment Manifest?

### Build Manifest

A **build manifest** is created by `strata build run` and captures:

- **Configuration snapshot** — Full `platform.json` with all resolved values
- **Build artifacts** — Terraform, Ansible, Helm, Docker Compose files (if generated)
- **Repository state** — Git commits for all solution repositories
- **Bill of Materials** — SBOM with component versions
- **Build environment** — Timestamp, user, version, environment variables
- **Policy results** — Any policy engine outputs (if enabled)

**Purpose:** Proves what was built, by whom, when, and from which source code.

**Example use case:** Your security team asks, "What was in the build artifact that went to staging?" You show them the build manifest with the exact platform.json snapshot and commit SHAs.

### Deploy Manifest

A **deploy manifest** is created by `strata deploy run` and captures everything that was deployed:

- **Exact configuration** — Full `platform.json` snapshot
- **Pinned versions** — Git commits for all repositories
- **Infrastructure state** — Provisioners, backends, resource counts
- **Container images** — Image references with digests
- **Audit metadata** — Timestamp, user, deployment status, stage results
- **Bill of Materials** — SBOM with component versions and vulnerabilities

### The Build → Deploy Workflow

```
graph LR
    A["strata build run"] -->|generates| B["Build Manifest<br/>config + SBOM + repos"]
    B -->|input to| C["strata deploy run"]
    C -->|extends| D["Deploy Manifest<br/>build manifest + results"]
    D -->|compliance evidence| E["Audit Trail"]
```

**Timeline:**

1. **Build phase** → Build manifest written to `.strata/build/{deployment}/manifest.json`
2. **Review & testing** → Inspect build manifest before approving to deploy
3. **Deploy phase** → Deploy manifest written to `.strata/deployments/{deployment}/{version}/`
4. **Audit** → Both manifests available for compliance queries

---

## Why Use Deployment Manifests?

### Compliance & Audit

**Regulatory requirements (NIS2, ISAE 3402 Type 2):**

- Immutable record of infrastructure changes
- Proof of authorization (user who deployed, timestamp)
- Version pinning (exact Git commits deployed)
- Configuration snapshots for forensic analysis

**Example:** An auditor asks, "What was deployed to production on June 15?" You answer:

```bash
# Find the manifest
ls .strata/deployments/prod_deployment/v2.3.0/
# 2024-06-15T14:32:45Z.json

# Show it
jq '.spec.artifacts.repositories' 2024-06-15T14:32:45Z.json
# {
#   "xyz-infrastructure": {
#     "url": "git@github.com:acme/xyz-infra.git",
#     "ref": "v2.3.0",
#     "commit": "a1b2c3d4e5f6g7h8..."  ← exact commit deployed
#   }
# }
```

### Operational Visibility

**Troubleshooting:**

- Compare manifests before/after a change to identify drift
- Trace stage failures with per-stage timing and outputs
- Correlate with Git history to understand what changed

**Example:**

```bash
# What changed between two deployments?
diff <(jq '.spec.artifacts.repositories' manifest1.json) \
     <(jq '.spec.artifacts.repositories' manifest2.json)

# What was the deployment status?
jq '.spec.status, .spec.stages[].status' manifest.json
# "success"
# "success"
# "success"
```

### Rollback & Recovery

**Manifest as rollback source:**

```bash
# Extract pinned versions from the last-known-good deployment
jq '.spec.artifacts.repositories | to_entries | .[].value.commit' \
  previous_deployment.json > rollback_commits.txt

# Or extract the entire platform.json to reapply:
jq '.spec.artifacts.platform.content' good_manifest.json > platform.json.bak
```

---

## Setup

### 1. Enable Manifests in Configuration

Create or update your configuration file to include manifest storage:

**Local filesystem:**

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: local_manifest_config
spec:
  manifest:
    type: local
    path: ".strata/deployments"
```

**GitOps (state repository):**

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: gitops_manifest_config
spec:
  repositories:
    - name: xyz-state-repo
      url: git@github.com:acme/xyz-state.git
      branch: main
      clone: true

  manifest:
    type: gitops
    path: "deployments"
    repository: xyz-state-repo
    branch: manifests
    tag: true
```

### 2. Reference in Your Deployment

Add the configuration to your deployment file:

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: prod_deployment
spec:
  configurations:
    - name: local_manifest_config  # or gitops_manifest_config
      source:
        type: local
        repository: /
        source_path: config/configurations/manifest_config.yaml
```

### 3. Deploy

```bash
strata deploy run -f deployments/prod.yaml
```

The manifest is written **automatically** upon completion (success or failure).

---

## Reading a Manifest

Manifests are JSON files with this structure:

```js
{
  "apiVersion": "strata.huybrechts.xyz/v1",
  "kind": "deployment-manifest",
  "meta": {
    "name": "prod_deployment",
    "labels": {
      "version": "2.3.0",
      "environment": "production"
    }
  },
  "spec": {
    "deployment_name": "prod_deployment",
    "workspace_name": "prod_workspace",
    "action": "deploy",
    "status": "success",
    "timestamp": "2024-06-17T10:45:33Z",
    "user": "ops-lead@acme.com",
    "platform_version": "1.2.0",
    
    "artifacts": {
      "platform": {
        "hash": "sha256:abc123...",
        "path": "build/platform.json",
        "content": { ... full platform.json ... }
      },
      
      "repositories": {
        "xyz-infrastructure": {
          "url": "git@github.com:acme/xyz-infra.git",
          "ref": "v2.3.0",
          "commit": "a1b2c3d4e5f6g7h8..."
        },
        "xyz-config": {
          "url": "git@github.com:acme/xyz-config.git",
          "ref": "main",
          "commit": "f7g8h9i0j1k2l3m4..."
        }
      },
      
      "images": [
        {
          "name": "traefik",
          "image": "docker.io/traefik:v3.0.1",
          "digest": "sha256:xyz789..."
        }
      ],
      
      "providers": [
        {
          "name": "tf_hetzner",
          "type": "terraform",
          "backend": {
            "type": "azurerm",
            "configuration": { ... }
          }
        }
      ],
      
      "sbom": {
        "path": "build/sbom.json",
        "format": "cyclonedx-1.6",
        "sha256": "sha256:def456...",
        "component_count": 47
      }
    },
    
    "stages": [
      {
        "name": "infrastructure",
        "status": "success",
        "duration_seconds": 125,
        "outputs": {
          "server_ip": "192.0.2.10",
          "load_balancer_fqdn": "lb.example.com"
        }
      },
      {
        "name": "configure",
        "status": "success",
        "duration_seconds": 45,
        "outputs": {}
      }
    ]
  }
}
```

### Key Fields

| Field                            | Purpose                                     |
| -------------------------------- | ------------------------------------------- |
| `spec.status`                    | Deployment outcome (`success` or `failure`) |
| `spec.timestamp`                 | When deployment ran (UTC, ISO 8601)         |
| `spec.user`                      | Who ran the deployment                      |
| `spec.artifacts.platform.hash`   | SHA-256 of the entire configuration         |
| `spec.artifacts.repositories`    | Git commits for audit trail                 |
| `spec.artifacts.images`          | Container image references + digests        |
| `spec.stages[].duration_seconds` | How long each stage took                    |
| `spec.stages[].outputs`          | Terraform outputs, Ansible facts, etc.      |
| `spec.artifacts.sbom`            | Bill of materials (vulnerabilities, etc.)   |

---

## Common Tasks

### List All Manifests

**Using `strata manifest` CLI:**

```bash
# List all available manifests
strata manifest list

# JSON output for scripting
strata manifest list --output json

# Filter by deployment
strata manifest list --deployment prod_deployment --output json

# Show last 5 manifests
strata manifest list --last 5
```

**Manual filesystem traversal:**

**Local storage:**

```bash
# List all manifests
find .strata/deployments -name "*.json" -type f | sort

# List just production deployments, newest first
find .strata/deployments/prod_deployment -name "*.json" | sort -r | head -5
```

**GitOps storage:**

```bash
# Clone the state repo
git clone git@github.com:acme/xyz-state.git
cd xyz-state

# List deployments
find deployments -name "*.json" | sort -r
```

### View a Manifest

**Using `strata manifest` CLI:**

```bash
# Show manifest in console-friendly format
strata manifest show 2024-06-17T10:45:33Z.json

# JSON output
strata manifest show 2024-06-17T10:45:33Z.json --output json

# Query specific fields with jq
strata manifest show 2024-06-17T10:45:33Z.json --output json | \
  jq '.spec.artifacts.repositories'
```

### Export Manifests for Compliance

**Create compliance evidence package:**

```bash
# Export all manifests with SBOM and platform.json
strata manifest export --output-dir ./compliance_package \
  --include-sbom \
  --include-platform

# Result:
# compliance_package/
#   manifests/
#     2024-06-17T10:45:33Z.json
#     2024-06-16T14:22:10Z.json
#   sboms/
#     2024-06-17T10:45:33Z-sbom.json
#   artifacts/
#     platform-2024-06-17T10:45:33Z.json
```

### Compare Two Deployments

```bash
# Export both manifests to JSON
manifest1=$(strata manifest show m1.json --output json)
manifest2=$(strata manifest show m2.json --output json)

# What repositories changed?
diff <(echo "$manifest1" | jq -r '.spec.artifacts.repositories | keys[]' | sort) \
     <(echo "$manifest2" | jq -r '.spec.artifacts.repositories | keys[]' | sort)

# What commits changed?
diff <(echo "$manifest1" | jq '.spec.artifacts.repositories') \
     <(echo "$manifest2" | jq '.spec.artifacts.repositories')
```

### Extract Terraform Outputs

```bash
# Get all stage outputs
jq '.spec.stages[] | select(.name == "infrastructure") | .outputs' manifest.json

# Get one specific output
jq '.spec.stages[] | select(.name == "infrastructure") | .outputs.server_ip' manifest.json
```

### Check Deployment Duration

```bash
# Total duration across all stages
jq '[.spec.stages[].duration_seconds] | add' manifest.json

# Duration per stage
jq '.spec.stages[] | {name, duration_seconds}' manifest.json
```

### Inspect SBOM

```bash
# Count components
jq '.spec.artifacts.sbom.component_count' manifest.json

# Get SBOM file
cat build/sbom.json | jq '.metadata.component'
```

### Failed Deployment Details

```bash
# Check status
jq '.spec.status' failed_manifest.json

# Which stage failed?
jq '.spec.stages[] | select(.status != "success")' failed_manifest.json

# Get error details (if captured)
jq '.spec.stages[] | select(.status != "success") | .error' failed_manifest.json
```

---

## GitOps Workflow

When using `type: gitops`, manifests are automatically committed and tagged:

```bash
# After deploy
strata deploy run -f prod.yaml

# In your state repository:
git log --oneline deployments/
# 2a3b4c5 strata: deployment manifest prod_deployment v2.3.0
# 1f2g3h4 strata: deployment manifest prod_deployment v2.2.9
# ...

# Tags are also created:
git tag -l "prod_deployment/*" | sort
# prod_deployment/v2.2.8
# prod_deployment/v2.2.9
# prod_deployment/v2.3.0

# Retrieve a manifest from a tag:
git show prod_deployment/v2.3.0:deployments/prod_deployment/v2.3.0/2024-06-17T10:45:33Z.json
```

### Downstream Automation

GitOps manifests enable automatic downstream workflows:

```bash
# Compliance scanner watches the state repo
# On new manifest commit → scan SBOM for vulnerabilities
# On new tag → generate audit report

# Example: GitHub Actions on manifest push
on:
  push:
    paths:
      - 'deployments/**'
jobs:
  compliance-scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Extract SBOM from manifest
        run: |
          manifest=$(ls -t deployments/prod_deployment/**/*.json | head -1)
          sbom_path=$(jq -r '.spec.artifacts.sbom.path' $manifest)
          # Run compliance scanner on sbom_path
```

---

## Troubleshooting

### Manifest Not Written

**Check configuration:**

```bash
# Verify the configuration exists
strata validate config/configurations/manifest_config.yaml

# Check for errors in the build log
strata deploy run -f prod.yaml --verbose 2>&1 | grep -i manifest
```

**Missing `manifest` section:**

If you see `Manifest configuration not found`, the deployment doesn't reference a configuration with manifest settings. Add it:

```yaml
spec:
  configurations:
    - name: my_manifest_config
      source:
        type: local
        repository: /
        source_path: config/configurations/manifest.yaml
```

### GitOps Push Failed

```bash
# Check git credentials
git clone git@github.com:acme/xyz-state.git

# Verify branch exists
git ls-remote origin manifests

# Check deploy logs for git errors
strata deploy run -f prod.yaml --verbose 2>&1 | grep -i "git\|push\|branch"
```

### Manifest Has Empty `artifacts.content`

**Cause:** Platform artifact wasn't generated.

**Fix:** Run `strata build run` before `strata deploy run`:

```bash
strata build run -f prod.yaml
strata deploy run -f prod.yaml
```

### Export Audit Trail with Manifests

```bash
# Export all deployment logs plus associated manifests
strata audit export --output-dir ./audit_export --include-manifests

# Result:
# audit_export/
#   deploy_logs/
#     log_001.json
#     log_002.json
#   manifests/
#     manifest_001.json
#     manifest_002.json
```

---

## Build Manifests

Build manifests are generated automatically during `strata build run` and stored in `.strata/build/{deployment}/manifest.json`. They capture the state **before** deployment.

### When are Build Manifests Created?

```bash
strata build run -f deploy.yaml
# Executes: platform builder → terraform builder → ansible builder → helm builder → sbom builder
# Writes: .strata/build/{deployment}/manifest.json (contains all build artifacts + SBOM reference)
```

### Build Manifest Structure

```text
{
  "apiVersion": "strata.huybrechts.xyz/v1",
  "kind": "deployment-manifest",
  "meta": {
    "name": "prod_deployment",
    "labels": {
      "version": "2.3.0",
      "environment": "production"
    }
  },
  "spec": {
    "deployment_name": "prod_deployment",
    "workspace_name": "prod_workspace",
    "action": "build",
    "status": "success",
    "timestamp": "2024-06-17T10:35:20Z",
    "user": "devops@acme.com",
    "platform_version": "1.2.0",
    "build_duration_seconds": 87,
    
    "artifacts": {
      "platform": {
        "hash": "sha256:abc123def456...",
        "path": ".strata/build/prod_deployment/platform.json",
        "content": { ...full platform.json snapshot... }
      },
      
      "repositories": {
        "xyz-infrastructure": {
          "url": "git@github.com:acme/xyz-infra.git",
          "ref": "v2.3.0",
          "commit": "a1b2c3d4e5f6g7h8..."
        }
      },
      
      "sbom": {
        "path": ".strata/build/prod_deployment/sbom.json",
        "format": "cyclonedx-1.6",
        "sha256": "sha256:xyz789..."
      }
    },
    
    "policy_results": {
      "status": "passed",
      "policies_checked": 12,
      "policies_passed": 12,
      "violations": []
    }
  }
}
```

### Key Differences from Deploy Manifests

| Aspect | Build | Deploy |
|--------|-------|--------|
| **Trigger** | `strata build run` | `strata deploy run` |
| **Stages** | None | Multiple (infrastructure, configure, etc.) |
| **Outputs** | SBOM, policy results | Stage outputs, resource IDs, IPs |
| **Errors** | Build/validation errors | Provisioner errors |
| **Purpose** | Gate before deploying | Record what was deployed |

### Querying Build Manifests

```bash
# List only build manifests (not deployment manifests)
find .strata/build -name "manifest.json" -type f

# Show details of a build
jq '.spec | {timestamp, user, build_duration_seconds, policy_results}' \
  .strata/build/prod_deployment/manifest.json

# Check if build passed all policies
jq '.spec.policy_results.status' .strata/build/prod_deployment/manifest.json
```

### Workflow: Review Build Before Deploying

```bash
# 1. Build artifacts
strata build run -f deploy/prod.yaml

# 2. Review the build manifest
strata manifest show .strata/build/prod_deployment/manifest.json

# 3. Check policy results
jq '.spec.policy_results' .strata/build/prod_deployment/manifest.json

# 4. If satisfied, deploy
strata deploy run -f deploy/prod.yaml
```

---

## Best Practices

1. **Store manifests in version control** — Commit local manifests to Git alongside your deployment configs.
2. **Tag important deployments** — Use Git tags in the state repo to mark production releases.
3. **Automate archive cleanup** — Old manifests accumulate; implement retention policies (e.g., keep last 30 days).
4. **Query manifests regularly** — Build alerts/dashboards around manifest status, duration, user, and configuration changes.
5. **Include in change tickets** — Reference the manifest commit hash in your change management system (e.g., "Changes applied in manifest commit abc123d").
6. **Review SBOM for vulnerabilities** — Extract and scan the SBOM component list for known CVEs after each deployment.

---

## See Also

- [Configuration → Manifest](../config/manifest.md) — Full schema reference
- [Models → deployment-manifest](../platform/models.md) — YAML kind specification
- [Architecture → Deployment Workflow](../platform/architecture.md) — Multi-repo design
- [Compliance & Audit](../platform/architecture.md#compliance--audit) — NIS2 / ISAE 3402 evidence
