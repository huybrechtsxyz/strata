# Deployment Manifests Guide

Learn how to enable, interpret, and use deployment manifests for compliance, auditing, and operational visibility.

---

## What is a Deployment Manifest?

A **deployment manifest** is an immutable snapshot captured by `strata deploy run` containing everything that was deployed:

- **Exact configuration** — Full `platform.json` snapshot
- **Pinned versions** — Git commits for all repositories
- **Infrastructure state** — Provisioners, backends, resource counts
- **Container images** — Image references with digests
- **Audit metadata** — Timestamp, user, deployment status, stage results
- **Bill of Materials** — SBOM with component versions and vulnerabilities

Think of it as **evidence** — a complete record of what was actually deployed, not just what was intended.

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

### List All Deployments

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

### Compare Two Deployments

```bash
# What repositories changed?
diff <(jq -r '.spec.artifacts.repositories | keys[]' manifest1.json | sort) \
     <(jq -r '.spec.artifacts.repositories | keys[]' manifest2.json | sort)

# What commits changed?
diff <(jq '.spec.artifacts.repositories' manifest1.json) \
     <(jq '.spec.artifacts.repositories' manifest2.json)
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
