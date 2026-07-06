# Deployment Manifest Schema

Complete schema reference for deployment manifests in JSON format.

---

## Root Structure

```text
{
  "apiVersion": "strata.huybrechts.xyz/v1",
  "kind": "deployment-manifest",
  "meta": { ... },
  "spec": { ... }
}
```

### `apiVersion`

**Type:** string

**Valid values:** `strata.huybrechts.xyz/v1`

**Description:** API version for manifest schema compatibility.

### `kind`

**Type:** string

**Valid values:** `deployment-manifest`

**Description:** Resource kind identifier.

---

## Metadata Section

```json
"meta": {
  "name": "prod_deployment",
  "annotations": {
    "description": "Production deployment manifest"
  },
  "labels": {
    "version": "2.3.0",
    "environment": "production"
  }
}
```

### `meta.name`

**Type:** string

**Pattern:** `^[a-z0-9]([a-z0-9-_]{0,61}[a-z0-9])?$`

**Description:** Name of the deployment. Lowercase alphanumeric, hyphens, and underscores.

### `meta.annotations`

**Type:** object (optional)

**Properties:**
- `description` (string, optional) — Human-readable description of the manifest

### `meta.labels`

**Type:** object (optional)

**Properties:**
- `version` (string, optional) — Semantic version of deployed artifacts
- `environment` (string, optional) — Environment name (e.g., "production", "staging")
- Custom labels — Any additional key-value pairs for filtering/tagging

---

## Specification Section

```text
"spec": {
  "deployment_name": "prod_deployment",
  "workspace_name": "prod_workspace",
  "action": "deploy",
  "status": "success",
  "timestamp": "2024-06-17T10:45:33Z",
  "user": "ops@acme.com",
  "platform_version": "1.2.0",
  "build_duration_seconds": 87,
  "deploy_duration_seconds": 215,
  
  "artifacts": { ... },
  "stages": [ ... ],
  "policy_results": { ... }
}
```

### Core Fields

#### `spec.deployment_name`

**Type:** string

**Description:** Name of the deployment resource being manifested.

#### `spec.workspace_name`

**Type:** string

**Description:** Name of the workspace where build/deploy occurred.

#### `spec.action`

**Type:** string

**Valid values:** `"build" | "deploy" | "destroy"`

**Description:** Type of action:
- `"build"` — Build manifest from `strata build run`
- `"deploy"` — Deploy manifest from `strata deploy run`
- `"destroy"` — Destruction record from `strata deploy destroy`

#### `spec.status`

**Type:** string

**Valid values:** `"success" | "failure" | "partial"`

**Description:** Outcome of action:
- `"success"` — Action completed successfully
- `"failure"` — Action failed
- `"partial"` — Some stages succeeded, some failed

#### `spec.timestamp`

**Type:** string (ISO 8601)

**Format:** `YYYY-MM-DDTHH:mm:ssZ`

**Example:** `"2024-06-17T10:45:33Z"`

**Description:** When the action occurred (UTC).

#### `spec.user`

**Type:** string

**Description:** Email or username of person who initiated the action.

#### `spec.platform_version`

**Type:** string

**Description:** Version of strata CLI that generated the manifest.

#### `spec.build_duration_seconds`

**Type:** integer (build manifests only)

**Description:** Total duration of build process in seconds.

#### `spec.deploy_duration_seconds`

**Type:** integer (deploy manifests only)

**Description:** Total duration of deployment process in seconds.

---

## Artifacts Section

```text
"artifacts": {
  "platform": { ... },
  "repositories": { ... },
  "images": [ ... ],
  "providers": [ ... ],
  "sbom": { ... }
}
```

### `artifacts.platform`

**Type:** object

```text
"platform": {
  "hash": "sha256:abc123def456...",
  "path": ".strata/build/prod_deployment/platform.json",
  "content": { ...full platform.json... }
}
```

**Properties:**

| Field | Type | Description |
|-------|------|-------------|
| `hash` | string | SHA-256 hash of platform.json file |
| `path` | string | Relative path to platform.json artifact |
| `content` | object | Full platform.json content (included for offline access) |

### `artifacts.repositories`

**Type:** object (map of repository name → commit info)

```json
"repositories": {
  "xyz-infrastructure": {
    "url": "git@github.com:acme/xyz-infra.git",
    "ref": "v2.3.0",
    "commit": "a1b2c3d4e5f6g7h8i9j0k1l2m3n4o5p6q7r8s9t0"
  },
  "xyz-config": {
    "url": "git@github.com:acme/xyz-config.git",
    "ref": "main",
    "commit": "f7g8h9i0j1k2l3m4n5o6p7q8r9s0t1u2v3w4x5y6"
  }
}
```

**Properties per repository:**

| Field | Type | Description |
|-------|------|-------------|
| `url` | string | Git remote URL |
| `ref` | string | Git ref (branch, tag, or commit SHA) |
| `commit` | string | Full commit SHA-1 hash |

### `artifacts.images`

**Type:** array of objects (optional)

```json
"images": [
  {
    "name": "traefik",
    "image": "docker.io/traefik:v3.0.1",
    "digest": "sha256:xyz789abc123..."
  },
  {
    "name": "app",
    "image": "ghcr.io/acme/app:v2.3.0",
    "digest": "sha256:123abc789xyz..."
  }
]
```

**Properties per image:**

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Human-readable image name |
| `image` | string | Full image reference with registry and tag |
| `digest` | string | Image content digest (SHA-256) |

### `artifacts.providers`

**Type:** array of objects (deploy manifests only)

```json
"providers": [
  {
    "name": "tf_hetzner",
    "type": "terraform",
    "backend": {
      "type": "local",
      "configuration": {
        "path": ".strata/build/prod_deployment/terraform"
      }
    }
  },
  {
    "name": "configure",
    "type": "ansible",
    "backend": {
      "type": "default",
      "configuration": {
        "playbook": "site.yml",
        "inventory": "inventory/hosts.yml"
      }
    }
  }
]
```

**Properties per provider:**

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Provisioner name from deployment spec |
| `type` | string | Provisioner type: `terraform`, `ansible`, `helm`, `compose` |
| `backend` | object | Backend configuration used |
| `backend.type` | string | Backend type (e.g., `local`, `azurerm`, `s3`) |
| `backend.configuration` | object | Backend-specific settings |

### `artifacts.sbom`

**Type:** object

```json
"sbom": {
  "path": ".strata/build/prod_deployment/sbom.json",
  "format": "cyclonedx-1.6",
  "sha256": "sha256:def456abc123...",
  "component_count": 47
}
```

**Properties:**

| Field | Type | Description |
|-------|------|-------------|
| `path` | string | Path to SBOM JSON file |
| `format` | string | SBOM format: `cyclonedx-1.6` (currently supported) |
| `sha256` | string | SHA-256 hash of SBOM file |
| `component_count` | integer | Number of components in SBOM |

---

## Stages Section

**Type:** array of objects (deploy manifests only)

```json
"stages": [
  {
    "name": "infrastructure",
    "status": "success",
    "duration_seconds": 125,
    "error": null,
    "outputs": {
      "server_ip": "192.0.2.10",
      "load_balancer_fqdn": "lb.example.com"
    }
  },
  {
    "name": "configure",
    "status": "success",
    "duration_seconds": 45,
    "error": null,
    "outputs": {}
  }
]
```

**Properties per stage:**

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Stage name from deployment spec |
| `status` | string | Outcome: `success`, `failure`, `skipped` |
| `duration_seconds` | integer | How long stage took |
| `error` | string \| null | Error message if failed, null if succeeded |
| `outputs` | object | Provisioner outputs (Terraform outputs, Ansible facts, etc.) |

---

## Policy Results Section

**Type:** object (build manifests only)

```json
"policy_results": {
  "status": "passed",
  "policies_checked": 12,
  "policies_passed": 12,
  "violations": []
}
```

**Properties:**

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Overall result: `passed`, `failed`, `warnings` |
| `policies_checked` | integer | Total policies evaluated |
| `policies_passed` | integer | Policies that passed |
| `violations` | array | List of policy violations (if any) |

**Violation structure:**

```json
{
  "policy_name": "no-public-access",
  "severity": "critical",
  "message": "Firewall rule allows public ingress on port 3306"
}
```

---

## Optional Sections

### Signatures (Future Support)

**Type:** object (optional, not yet implemented)

```json
"signatures": {
  "gpg": {
    "method": "gpg",
    "key_id": "0x1234567890ABCDEF",
    "signature": "-----BEGIN PGP SIGNATURE-----\n...\n-----END PGP SIGNATURE-----",
    "signed_at": "2024-06-17T10:45:33Z"
  }
}
```

**Purpose:** GPG-signed manifests for cryptographic verification (enables future compliance features).

---

## Complete Example: Build Manifest

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
        "content": {
          "apiVersion": "strata.huybrechts.xyz/v1",
          "kind": "deployment",
          "meta": {
            "name": "prod_deployment"
          },
          "spec": {
            "namespace": "default",
            "configurations": [ ... ]
          }
        }
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
        "sha256": "sha256:def456...",
        "component_count": 47
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

---

## Complete Example: Deploy Manifest

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
    "action": "deploy",
    "status": "success",
    "timestamp": "2024-06-17T10:45:33Z",
    "user": "ops@acme.com",
    "platform_version": "1.2.0",
    "deploy_duration_seconds": 215,
    
    "artifacts": {
      "platform": {
        "hash": "sha256:abc123def456...",
        "path": ".strata/build/prod_deployment/platform.json",
        "content": { ... }
      },
      
      "repositories": {
        "xyz-infrastructure": {
          "url": "git@github.com:acme/xyz-infra.git",
          "ref": "v2.3.0",
          "commit": "a1b2c3d4e5f6g7h8..."
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
            "type": "local",
            "configuration": {
              "path": ".strata/build/prod_deployment/terraform"
            }
          }
        }
      ],
      
      "sbom": {
        "path": ".strata/build/prod_deployment/sbom.json",
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
        "error": null,
        "outputs": {
          "server_ip": "192.0.2.10",
          "load_balancer_fqdn": "lb.example.com"
        }
      },
      {
        "name": "configure",
        "status": "success",
        "duration_seconds": 45,
        "error": null,
        "outputs": {}
      }
    ]
  }
}
```

---

## Validation Rules

| Rule | Description |
|------|-------------|
| `apiVersion` must be `strata.huybrechts.xyz/v1` | Version compatibility |
| `kind` must be `deployment-manifest` | Resource type validation |
| `meta.name` must match pattern `^[a-z0-9]...` | Kubernetes naming convention |
| `action` must be one of `build\|deploy\|destroy` | Valid action types |
| `status` must be one of `success\|failure\|partial` | Valid status values |
| `timestamp` must be valid ISO 8601 | Audit trail precision |
| `artifacts.platform.content` required if `action="build"` | Build artifact evidence |
| `stages` required if `action="deploy"` | Deploy execution record |

---

## Type Definitions

### GitCommit

```typescript
interface GitCommit {
  url: string              // Git remote URL
  ref: string             // Branch, tag, or commit SHA
  commit: string          // Full commit SHA-1 (40 chars)
}
```

### ContainerImage

```typescript
interface ContainerImage {
  name: string            // Image name (no registry)
  image: string           // Full reference: registry/image:tag
  digest: string          // SHA-256 digest: sha256:...
}
```

### ProvisionerInfo

```typescript
interface ProvisionerInfo {
  name: string
  type: "terraform" | "ansible" | "helm" | "compose"
  backend: {
    type: string
    configuration: Record<string, unknown>
  }
}
```

### SbomReference

```typescript
interface SbomReference {
  path: string            // File path to SBOM
  format: "cyclonedx-1.6" // SBOM format
  sha256: string          // File hash
  component_count: number
}
```

### PlatformArtifact

```typescript
interface PlatformArtifact {
  hash: string            // SHA-256 of platform.json
  path: string            // File path to platform.json
  content: Record<string, unknown>  // Full platform.json snapshot
}
```

### DeploymentStage

```typescript
interface DeploymentStage {
  name: string
  status: "success" | "failure" | "skipped"
  duration_seconds: number
  error: string | null
  outputs: Record<string, unknown>
}
```

### PolicyResult

```typescript
interface PolicyResult {
  status: "passed" | "failed" | "warnings"
  policies_checked: number
  policies_passed: number
  violations: {
    policy_name: string
    severity: "critical" | "high" | "medium" | "low"
    message: string
  }[]
}
```

---

## See Also

- [Deployment Manifests Guide](./deployment-manifests.md) — How to generate and use manifests
- [Manifest CLI Reference](./manifest-cli.md) — CLI commands for manifest operations
- [ADR 0021: Manifests as First-Class Artifacts](../decisions/0021-deployment-manifests-as-first-class-build-artifacts.md) — Design rationale
