# Promotion Record

A **promotion record** (kind: `promotion-record`) is a machine-written audit log documenting a version promotion through a progression. It captures what was promoted, how, gates evaluated, and final outcome.

## Purpose

Promotion records enable:

- **Audit trail** — complete history of when, who, and what was promoted
- **Compliance evidence** — gate evaluation results prove promotion gates were checked
- **Rollback tracking** — records link promotion to rollback operations
- **Deployment linking** — records reference deployment manifests created during promotion
- **Version lineage** — track version progression through rings (dev → staging → prod)

## Conceptual Model

| Entity                  | Role                      | When Created                                    |
| ----------------------- | ------------------------- | ----------------------------------------------- |
| **Version Manifest**    | Input: desired versions   | Operator edits                                  |
| **Promotion Record**    | Output: audit log         | `strata promote start`                          |
| **Version Lock**        | Snapshot: frozen versions | `strata promote` writes per-ring lock           |
| **Deployment Manifest** | Deployment artifact       | `strata deploy run` references promotion record |

A promotion record documents the **entire promotion workflow** — from initiation through all ring waves to final outcome.

## Schema

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: promotion-record
meta:
  name: <record_name>            # e.g., prom-20260623-prd-001
  labels:
    target: <target_name>        # e.g., app, traefik
    ring: <ring_name>            # Target ring (e.g., prd)
    outcome: <outcome>           # completed | partial | rolled-back
  annotations:
    description: <description>
spec:
  # What was promoted
  target:
    type: remote | helm_chart | image
    name: <name>
    from_version: <version>      # Previous version
    to_version: <version>        # New version
  
  # How & where
  strategy: <strategy_name>      # Promotion strategy name
  progression: <progression_name>
  rings: [list]                  # Ordered rings from progression
  
  # Outcome
  outcome: completed | partial | rolled-back
  rollback_of: <promotion_record_name>  # If this is a rollback
  
  # Identity & timing
  initiated_by: <user>           # $USER or CI actor
  hostname: <hostname>           # Machine that ran strata
  started_at: <iso8601_utc>      # First wave commit timestamp
  completed_at: <iso8601_utc>    # Last wave commit timestamp (if completed)
  duration_seconds: <int>        # Calendar time elapsed
  
  # Git
  branch: promote/{target}-{version}-{ring}  # Promotion branch name
  commits:                       # Per-wave commits
    - ring_wave: <number>
      sha: <commit_sha>
      message: <message>
      committed_at: <iso8601_utc>
  
  # Gate results (compliance evidence)
  gates:                         # Gate evaluation results
    - gate: <gate_name>
      ring: <ring>
      require: any_one | all
      checked_at: <iso8601_utc>
      passed: <boolean>
      detail: <explanation>
  
  # Ring wave execution
  ring_waves:                    # Summary of each wave
    - ring_wave: <number>
      environments: [list]
      deployment_wave: <wave_name>
      deployments: <names_or_all>
      files_modified: [list]
      fields_removed: [list]
      committed_at: <iso8601_utc>
  
  # Links to deployment manifests
  deployment_manifests: [list]   # Paths to .strata/manifests/ files created by deploy
```

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: promotion-record
meta:
  name: prom-20260721-app-prd-001
  labels:
    target: app
    ring: prd
    outcome: completed
  annotations:
    description: "Promotion of app from v2.1.0 to v2.2.0 through dev→staging→prd"
spec:
  target:
    type: image
    name: app
    from_version: v2.1.0
    to_version: v2.2.0
  
  strategy: blue-green
  progression: prd-progression
  rings: [dev, staging, prd]
  
  outcome: completed
  
  initiated_by: alice
  hostname: ci-runner-1
  started_at: "2026-07-21T14:23:45Z"
  completed_at: "2026-07-21T14:35:12Z"
  duration_seconds: 687
  
  branch: promote/app-v2.2.0-prd
  commits:
    - ring_wave: 1
      sha: abc123def456...
      message: "Wave 1: dev - app v2.1.0→v2.2.0"
      committed_at: "2026-07-21T14:24:00Z"
    - ring_wave: 2
      sha: def456abc789...
      message: "Wave 2: staging - app v2.1.0→v2.2.0"
      committed_at: "2026-07-21T14:29:15Z"
    - ring_wave: 3
      sha: abc789def012...
      message: "Wave 3: prd - app v2.1.0→v2.2.0"
      committed_at: "2026-07-21T14:35:00Z"
  
  gates:
    - gate: require_progression_order
      ring: dev
      require: any_one
      checked_at: "2026-07-21T14:23:50Z"
      passed: true
    - gate: require_progression_order
      ring: staging
      require: any_one
      checked_at: "2026-07-21T14:29:10Z"
      passed: true
    - gate: require_progression_order
      ring: prd
      require: all
      checked_at: "2026-07-21T14:34:50Z"
      passed: true
  
  ring_waves:
    - ring_wave: 1
      environments: [dev]
      deployment_wave: all
      deployments: all
      files_modified:
        - versions/dev.yaml
      committed_at: "2026-07-21T14:24:00Z"
    - ring_wave: 2
      environments: [staging]
      deployment_wave: all
      deployments: all
      files_modified:
        - versions/staging.yaml
      committed_at: "2026-07-21T14:29:15Z"
    - ring_wave: 3
      environments: [prd]
      deployment_wave: canary
      deployments: [canary-deployment]
      files_modified:
        - versions/prd.yaml
      committed_at: "2026-07-21T14:35:00Z"
  
  deployment_manifests:
    - .strata/manifests/prom-20260721-app-prd-001/dev-manifest.yaml
    - .strata/manifests/prom-20260721-app-prd-001/staging-manifest.yaml
    - .strata/manifests/prom-20260721-app-prd-001/prd-manifest.yaml
```

## Key Fields

| Field                       | Type   | Required | Description                                                          |
| --------------------------- | ------ | -------- | -------------------------------------------------------------------- |
| `meta.name`                 | string | Yes      | Unique record identifier (e.g., `prom-{date}-{target}-{ring}-{seq}`) |
| `meta.labels.target`        | string | No       | Artifact name being promoted (for filtering)                         |
| `meta.labels.ring`          | string | No       | Target ring (for filtering)                                          |
| `meta.labels.outcome`       | string | No       | Outcome status (for filtering)                                       |
| `spec.target`               | object | Yes      | What was promoted: type, name, from/to versions                      |
| `spec.strategy`             | string | Yes      | Promotion strategy name                                              |
| `spec.progression`          | string | Yes      | Progression name                                                     |
| `spec.outcome`              | string | Yes      | `completed` \| `partial` \| `rolled-back`                            |
| `spec.initiated_by`         | string | Yes      | User or CI actor                                                     |
| `spec.started_at`           | string | Yes      | ISO 8601 UTC timestamp                                               |
| `spec.branch`               | string | Yes      | Promotion branch name                                                |
| `spec.gates`                | array  | No       | Gate evaluation results (compliance evidence)                        |
| `spec.ring_waves`           | array  | No       | Per-wave execution summaries                                         |
| `spec.deployment_manifests` | array  | No       | Links to deployment manifests                                        |

## Workflow

### Initiating a Promotion

```bash
strata promote start --target image:app:v2.2.0 --progression prd-progression
# Writes promotion-record at .strata/promotions/records/
```

### Viewing Promotion History

```bash
# List all promotion records
strata promote history

# View details of a specific record
strata promote show prom-20260721-app-prd-001
```

### Rollback

```bash
strata promote rollback prom-20260721-app-prd-001
# Creates new promotion-record with rollback_of reference
# Restores previous version state
```

## Storage

Promotion records are stored locally in `.strata/promotions/records/` and are typically committed to git as part of the promotion branch. They provide a complete audit trail of all promotions.

## Relationship to Other Kinds

- **Version Manifest** → Input: desired versions
- **Promotion Record** → Output: audit log of promotion workflow
- **Version Lock** → Snapshot: frozen versions per-ring after promotion
- **Deployment Manifest** → Final artifact: infrastructure to be deployed
