# Version Manifest

A **version manifest** (kind: `version`) declares intended versions for a deployment ring. It's a human- and tool-editable file that serves as the single source of truth for infrastructure component versions across a ring.

## Purpose

Version manifests enable:

- **Centralized version control** — one place to declare and update all component versions (images, Helm charts, infrastructure-as-code modules)
- **Version progression** — feed manifests into the promotion pipeline to advance versions through rings (dev → staging → prod)
- **Operator control** — operators or external tooling (CI, version management bots) can edit the manifest directly
- **Audit trail** — git history tracks version changes and who made them

## Conceptual Model

| File                 | Author         | Purpose                                                |
| -------------------- | -------------- | ------------------------------------------------------ |
| **Version Manifest** | Human / Tools  | Desired versions (editable, operator-controlled)       |
| **Version Lock**     | strata promote | Frozen snapshot of deployed versions (machine-written) |

The manifest is the **input**; the lock is the **output** after promotion or manual locking.

## Schema

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: version
meta:
  name: <ring_name>              # Required: ring identifier (e.g., 'dev', 'prd')
  annotations:
    description: <description>
  labels:
    version: "<version>"         # Optional labels
spec:
  ring: <ring_name>              # Required: ring identifier (same as meta.name)
  pins:
    images:                       # Optional: container images by service/module
      <service_name>: <tag>
    charts:                       # Optional: Helm chart versions by module
      <module_name>: <version>
    remotes:                      # Optional: git remote versions (tags, branches, SHAs)
      <remote_name>: <ref>
    tools:                        # Optional: provisioner tool versions
      <provisioner_name>: <version>
  hash: <sha256>                 # Optional: written by 'strata versions lock' for tamper detection
```

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: version
meta:
  name: dev
  annotations:
    description: "Development ring versions"
  labels:
    environment: development
spec:
  ring: dev
  pins:
    images:
      app: v2.1.0
      worker: v2.1.0
    charts:
      traefik: "28.2.0"
      cert-manager: "1.16.0"
    remotes:
      iac_core: v2.6.0
```

## Key Fields

| Field               | Type   | Required | Description                                                        |
| ------------------- | ------ | -------- | ------------------------------------------------------------------ |
| `meta.name`         | string | Yes      | Ring identifier (e.g., `dev`, `staging`, `prd`)                    |
| `spec.ring`         | string | Yes      | Ring being versioned (must match a ring in the progression)        |
| `spec.pins.images`  | dict   | No       | Container image versions keyed by service/module name              |
| `spec.pins.charts`  | dict   | No       | Helm chart versions keyed by module name                           |
| `spec.pins.remotes` | dict   | No       | Git remote versions keyed by remote name (tags, branches, SHAs)    |
| `spec.pins.tools`   | dict   | No       | Provisioner tool versions keyed by provisioner name                |
| `spec.hash`         | string | No       | SHA-256 hash written by `strata versions lock` to detect tampering |

## Workflow

### Creating a Manifest

```bash
# Initialize a new version manifest for a ring
strata versions init --ring dev --output versions/dev.yaml
```

### Updating Versions

Edit the manifest directly or use external tooling:

```yaml
spec:
  pins:
    images:
      app: v2.2.0          # Update image tag
```

### Locking a Manifest

Convert a manifest into an immutable lock file:

```bash
# Generate a version-lock from this manifest
strata versions lock --from versions/dev.yaml --output versions/dev.lock.yaml
```

### Applying in Deployments

Reference the manifest in a deployment:

```yaml
kind: deployment
spec:
  properties:
    versions:
      manifest: versions/dev.yaml
```

## Relationship to Version Lock

- **Version Manifest** (`kind: version`) — human-editable input
- **Version Lock** (`kind: version-lock`) — machine-written snapshot

After promotion, manifests are typically frozen into locks. Locks serve as the immutable record of what was deployed.
