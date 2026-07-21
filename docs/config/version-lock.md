# Version Lock

A **version lock** (kind: `version-lock`) is a frozen snapshot of component versions for a deployment ring. It's machine-written by the promotion pipeline and serves as the immutable record of what was deployed.

## Purpose

Version locks enable:

- **Immutable deployment records** — lock captures exactly what versions were deployed
- **Promotion state tracking** — each ring has a lock capturing its current version state
- **Rollback capability** — locks record previous version state for rollback operations
- **Tamper detection** — SHA-256 hash validation ensures locks haven't been modified
- **Thin references** — locks can point to version manifests instead of duplicating pins

## Conceptual Model

| File                 | Author                                    | Mutability | Purpose                                           |
| -------------------- | ----------------------------------------- | ---------- | ------------------------------------------------- |
| **Version Manifest** | Operator / Tools                          | Editable   | Source of truth for desired versions              |
| **Version Lock**     | `strata promote` / `strata versions lock` | Immutable  | Frozen snapshot after promotion or manual locking |

## Schema

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: version-lock
meta:
  name: <ring_name>              # Required: ring.scope identifier (e.g., 'prd', 'prd.us-east-1')
  annotations:
    description: <description>
  labels:
    environment: <env>
spec:
  ring: <ring_name>              # Required: ring being locked
  source: <path>                 # Optional: relative path to version manifest (thin reference)
  hash: <sha256>                 # Optional: SHA-256 of the source manifest's spec.pins
  pins:                          # Optional (if source omitted): inline version pins
    - target:
        type: image | helm_chart | remote | tool
        name: <name>
      version: <version>         # Exact version string
      track: exact | latest      # exact = pinned; latest = floating (dev rings only)
      resolved: <version>        # Last known resolved (for track: latest)
      resolved_sha: <sha>        # Immutable SHA for tamper detection
      resolved_at: <timestamp>   # When resolved was recorded
  previous:                      # Optional: prior ring lock state (for rollback)
    source: <path>
    version: <version>
    hash: <sha256>
```

## Example — Thin Reference (Promotion)

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: version-lock
meta:
  name: prd
  annotations:
    description: "Production lock"
  labels:
    environment: production
spec:
  ring: prd
  source: prd.yaml               # Points to version manifest
  hash: "abc123def456..."        # Hash of the manifest's spec.pins
  previous:
    source: prd.yaml
    version: v2.0.0
    hash: "old_hash..."
```

## Example — Inline Pins (Manual Lock)

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: version-lock
meta:
  name: dev
spec:
  ring: dev
  pins:
    - target:
        type: image
        name: app
      version: v2.1.0
      track: exact
      resolved_sha: "sha256:abc123..."
    - target:
        type: helm_chart
        name: traefik
      version: "28.2.0"
      track: exact
      resolved_sha: "sha256:def456..."
```

## Key Fields

| Field           | Type   | Required    | Description                                                                        |
| --------------- | ------ | ----------- | ---------------------------------------------------------------------------------- |
| `meta.name`     | string | Yes         | Ring identifier or `ring.scope` (e.g., `prd`, `prd.us-east-1`)                     |
| `spec.ring`     | string | Yes         | Ring being locked (must match a ring in the progression)                           |
| `spec.source`   | string | Conditional | Relative path to version manifest (thin reference); mutually exclusive with `pins` |
| `spec.hash`     | string | No          | SHA-256 of the referenced manifest's `spec.pins` (tamper detection)                |
| `spec.pins`     | array  | Conditional | Inline version pins (old-style); mutually exclusive with `source`                  |
| `spec.previous` | object | No          | Prior ring lock snapshot (enables single-step rollback)                            |

## Workflow

### Manual Locking

Create a lock from a version manifest:

```bash
strata versions lock --from versions/dev.yaml --output versions/dev.lock.yaml
```

### Promotion

Promotion automatically writes locks:

```bash
strata promote start --target image:app:v2.2.0 --progression prd-progression
# Writes version-lock files for each ring wave
```

### Validation

Validate a lock file hasn't been tampered with:

```bash
strata validate versions/prd.lock.yaml --deep
# Checks SHA-256 hash if present
```

### Rollback

Rollback to previous version state:

```bash
strata promote rollback versions/prd.lock.yaml
# Uses spec.previous to restore prior lock
```

## Relationship to Version Manifest

- **Manifest** → manual, human-editable, source of truth for desired versions
- **Lock** → frozen output from promotion, immutable record of deployed versions

Locks typically reference manifests via `spec.source` (thin reference) to avoid duplication. When manifests change, the lock continues pointing to the old version until promotion creates a new lock.
