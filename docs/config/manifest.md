# Deployment Manifest Configuration

Control how deployment manifests are written after successful or failed `strata deploy run` commands. Deployment manifests capture audit-ready evidence: Git commits, version tags, timestamps, user, complete configuration, infrastructure state, and SBOM data.

## Purpose

When enabled, deployment manifests provide:

- **Compliance audit trail** — Exact infrastructure state at deploy time (NIS2, ISAE 3402 Type 2)
- **Deployment history** — Track what was deployed, when, by whom, and what changed
- **Rollback reference** — Pin exact versions for recovery
- **Forensic analysis** — Investigate drift or failed deployments

When omitted from configuration, manifests are not written and a log message is emitted.

## Schema

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: <name>
spec:
  manifest:
    type: local | gitops              # Storage backend (required)
    path: <path>                      # Base directory (required)
    repository: <repository_name>     # Repo name for gitops (required if type=gitops)
    branch: <branch_name>             # Target branch (required if type=gitops)
    tag: true | false                 # Create git tag (gitops only, default: true)
```

## Storage Types

### Local Filesystem Storage

Write manifests to a local directory within the workspace:

```yaml
manifest:
  type: local
  path: ".strata/deployments"
```

**Output structure:**
```
.strata/deployments/
  ├── my_deployment_v1.0.0_2024-06-15T14:32:45Z.json
  ├── my_deployment_v1.0.1_2024-06-16T09:15:22Z.json
  └── prod_deployment_v2.3.0_2024-06-17T10:45:33Z.json
```

**Path resolution:**

The service auto-appends `/{deployment_name}/{version}/{timestamp}.json` to the base path:

```
{path}/{deployment_name}/{version}/{timestamp}.json
```

**Use when:**
- Single-environment or local deployments
- Manifests are committed to Git separately
- Minimal external dependencies

---

### GitOps Repository Storage

Commit manifests automatically to a Git repository (state repo pattern):

```yaml
manifest:
  type: gitops
  path: "deployments"                           # Directory in target repo
  repository: xyz-state-repo                    # Remote name (must be in spec.remotes)
  branch: manifests                             # Target branch
  tag: true                                     # Create git tag after commit
```

**Output structure:**
```
xyz-state-repo/
  manifests/
    ├── my_deployment/
    │   ├── v1.0.0/
    │   │   └── 2024-06-15T14:32:45Z.json
    │   └── v1.0.1/
    │       └── 2024-06-16T09:15:22Z.json
    └── prod_deployment/
        └── v2.3.0/
            └── 2024-06-17T10:45:33Z.json
```

**Git workflow:**

```bash
# Service creates/updates manifest file
# Commits to specified branch with message:
git add deployments/<deployment_name>/<version>/<timestamp>.json
git commit -m "strata: deployment manifest <deployment_name> v<version>"

# Optionally creates annotated tag:
git tag -a <deployment_name>/<version> -m "Deployed at <timestamp>"
git push origin <branch_name> --tags
```

**Repository requirement:**

The `repository` field must reference a registered remote in `spec.remotes`:

```yaml
spec:
  remotes:
    - name: xyz-state-repo
      url: git@github.com:org/xyz-state-repo.git
      branch: main
      clone: true
      
  manifest:
    type: gitops
    path: deployments
    repository: xyz-state-repo              # ← must match a registered repo
    branch: manifests                       # ← branch in that repo
    tag: true
```

**Use when:**
- Multi-environment GitOps workflows
- Compliance requires immutable audit trail in version control
- Multiple teams need shared deployment history
- Automated downstream processes consume manifests (e.g., compliance scanners)

---

## Manifest Content

Every manifest captures:

| Field                      | Contents                                       | Purpose                                      |
| -------------------------- | ---------------------------------------------- | -------------------------------------------- |
| **deployment_name**        | Name of the deployment                         | Identification                               |
| **workspace_name**         | Referenced workspace name                      | Traceability                                 |
| **action**                 | `deploy` or `destroy`                          | Operation type                               |
| **status**                 | `success` or `failure`                         | Outcome                                      |
| **timestamp**              | ISO 8601 timestamp (UTC)                       | When it happened                             |
| **user**                   | Git user.name from `.gitconfig`                | Who deployed it                              |
| **platform**               | Platform version                               | Audit trail                                  |
| **artifacts.platform**     | Hash + full `platform.json` content            | Complete configuration snapshot              |
| **artifacts.repositories** | Pinned commits for all referenced repos        | Version control proof                        |
| **artifacts.images**       | Container images with digests (if applicable)  | Application versions                         |
| **artifacts.providers**    | All provisioners, backends, and state backends | Infrastructure tooling                       |
| **artifacts.sbom**         | Software Bill of Materials (CycloneDX format)  | Component inventory & vulnerability tracking |
| **stages**                 | Per-stage status, duration, outputs            | Execution details                            |

## Examples

### Local — Development

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: dev_manifest
spec:
  manifest:
    type: local
    path: ".strata/deployments"
```

Run:
```bash
strata deploy run -f deployments/dev.yaml
# Writes: .strata/deployments/dev_v0.1.0_2024-06-15T14:32:45Z.json
```

### GitOps — Multi-Environment

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: prod_manifest
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

Run:
```bash
strata deploy run -f deployments/prod.yaml
# Commits to xyz-state-repo/manifests/prod_v2.3.0/2024-06-17T10:45:33Z.json
# Tags: prod/v2.3.0
# Pushes: git push origin manifests --tags
```

---

## Troubleshooting

| Issue                        | Cause                                | Solution                                                   |
| ---------------------------- | ------------------------------------ | ---------------------------------------------------------- |
| Manifest not written         | `manifest` section missing in config | Add `manifest` section with `type` and `path`              |
| gitops type fails to push    | Remote not registered                | Verify `repository` field matches a name in `spec.remotes` |
| gitops fails: branch not set | Required field missing               | Add `branch` field when `type: gitops`                     |
| Manifest has empty `content` | Platform artifact not generated      | Run `strata build run` before `strata deploy run`          |

---

## Integration with Deployment Configuration

Manifests are **not** defined in the deployment YAML itself — they are controlled by the **configuration** file that the deployment uses. This allows:

- Reuse of the same deployment manifest config across multiple deployments
- Central audit policy (one configuration file per environment or team)
- Flexible storage switching without rewriting deployment files

**Typical layout:**

```
config/
  configurations/
    prod_manifest.yaml          ← defines manifest storage
  deployments/
    prod_deployment.yaml        ← references the configuration
    staging_deployment.yaml
```

**In prod_deployment.yaml:**

```yaml
spec:
  configurations:
    - name: prod_manifest
      source:
        type: local
        repository: /
        source_path: config/configurations/prod_manifest.yaml
```
