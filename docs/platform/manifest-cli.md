# Manifest CLI Reference

Complete reference for the `strata manifest` command group, used to query, display, and export deployment manifests.

---

## Commands

### `strata manifest list`

**List all available manifests from build and deployment outputs.**

```bash
strata manifest list [OPTIONS]
```

#### Options

| Flag                | Type    | Description                                    |
| ------------------- | ------- | ---------------------------------------------- |
| `--deployment NAME` | string  | Filter manifests by deployment name (optional) |
| `--last N`          | integer | Show only the last N manifests (optional)      |
| `--output FORMAT`   | string  | Output format: `console` (default) or `json`   |
| `--work-path PATH`  | string  | Workspace root (default: auto-detect)          |
| `--verbose`         | flag    | Verbose output                                 |
| `--quiet`           | flag    | Suppress output                                |

#### Output

**Console format:**

```
Deployment Manifests
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 Path                                              Deployment  Action  Status  User
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 .strata/build/prod/manifest.json                 prod        build   success ops@acme.com
 .strata/deployments/prod/v2.3.0/2024-06-17.json  prod        deploy  success ops@acme.com
 .strata/deployments/prod/v2.2.9/2024-06-16.json  prod        deploy  success ops@acme.com
```

**JSON format:**

```json
[
  {
    "path": ".strata/build/prod/manifest.json",
    "deployment": "prod",
    "action": "build",
    "status": "success",
    "timestamp": "2024-06-17T10:35:20Z",
    "user": "ops@acme.com"
  },
  {
    "path": ".strata/deployments/prod/v2.3.0/2024-06-17T10:45:33Z.json",
    "deployment": "prod",
    "action": "deploy",
    "status": "success",
    "timestamp": "2024-06-17T10:45:33Z",
    "user": "ops@acme.com"
  }
]
```

#### Examples

```bash
# List all manifests
strata manifest list

# List only production manifests
strata manifest list --deployment prod

# List last 5 manifests, JSON output
strata manifest list --last 5 --output json

# Query with jq
strata manifest list --output json | jq '.[] | select(.action == "build")'
```

---

### `strata manifest show`

**Display details of a single manifest.**

```bash
strata manifest show <manifest-path> [OPTIONS]
```

#### Arguments

| Arg             | Description                                  |
| --------------- | -------------------------------------------- |
| `manifest-path` | Path to manifest file (relative or absolute) |

#### Options

| Flag               | Type   | Description                                  |
| ------------------ | ------ | -------------------------------------------- |
| `--output FORMAT`  | string | Output format: `console` (default) or `json` |
| `--work-path PATH` | string | Workspace root (default: auto-detect)        |
| `--verbose`        | flag   | Verbose output                               |

#### Output

**Console format:**

```
Deployment Manifest
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Deployment:     prod
Action:         build
Status:         success
Timestamp:      2024-06-17T10:35:20Z
User:           ops@acme.com
Version:        2.3.0
Duration:       87 seconds

Build Artifacts
─────────────────────────────────────────────────────────
Platform Hash:  sha256:abc123def456...
SBOM Format:    cyclonedx-1.6
SBOM Path:      .strata/build/prod/sbom.json

Repositories
─────────────────────────────────────────────────────────
xyz-infrastructure
  URL:    git@github.com:acme/xyz-infra.git
  Ref:    v2.3.0
  Commit: a1b2c3d4e5f6g7h8...

xyz-config
  URL:    git@github.com:acme/xyz-config.git
  Ref:    main
  Commit: f7g8h9i0j1k2l3m4...

Policy Results
─────────────────────────────────────────────────────────
Status:         passed
Policies:       12/12 passed
Violations:     0
```

**JSON format:**

Full manifest JSON structure (see [Manifest Structure](./deployment-manifests.md#manifest-structure)).

#### Examples

```bash
# Show manifest in console format
strata manifest show .strata/build/prod/manifest.json

# Show manifest as JSON
strata manifest show .strata/build/prod/manifest.json --output json

# Extract specific fields with jq
strata manifest show manifest.json --output json | jq '.spec.artifacts.repositories'

# Check policy results
strata manifest show manifest.json --output json | jq '.spec.policy_results'

# View all repository commits
strata manifest show manifest.json --output json | \
  jq '.spec.artifacts.repositories | to_entries[] | {repo: .key, commit: .value.commit}'
```

---

### `strata manifest export`

**Export manifests to a directory for compliance evidence packaging.**

```bash
strata manifest export [OPTIONS]
```

#### Options

| Flag                 | Type   | Description                                              |
| -------------------- | ------ | -------------------------------------------------------- |
| `--out PATH`         | string | Output directory (required)                              |
| `--include-sbom`     | flag   | Include SBOM files alongside manifests                   |
| `--include-platform` | flag   | Include platform.json artifacts alongside manifests      |
| `--work-path PATH`   | string | Workspace root (default: auto-detect)                    |
| `--output FORMAT`    | string | Output format for summary: `console` (default) or `json` |
| `--verbose`          | flag   | Verbose output                                           |

#### Output Structure

**Basic export:**

```
compliance_package/
├── manifests/
│   ├── prod-build-2024-06-17.json
│   ├── prod-deploy-2024-06-17.json
│   └── prod-deploy-2024-06-16.json
└── summary.json
```

**With `--include-sbom`:**

```
compliance_package/
├── manifests/
│   ├── prod-build-2024-06-17.json
│   └── prod-deploy-2024-06-17.json
├── sboms/
│   ├── prod-build-2024-06-17-sbom.json
│   └── prod-deploy-2024-06-17-sbom.json
└── summary.json
```

**With `--include-sbom --include-platform`:**

```
compliance_package/
├── manifests/
│   ├── prod-build-2024-06-17.json
│   └── prod-deploy-2024-06-17.json
├── sboms/
│   ├── prod-build-2024-06-17-sbom.json
│   └── prod-deploy-2024-06-17-sbom.json
├── artifacts/
│   ├── platform-prod-build-2024-06-17.json
│   └── platform-prod-deploy-2024-06-17.json
└── summary.json
```

#### Summary File

```json
{
  "export_timestamp": "2024-06-17T14:30:00Z",
  "exported_by": "ops@acme.com",
  "manifest_count": 10,
  "sbom_count": 5,
  "artifacts_count": 3,
  "manifests": [
    {
      "path": "manifests/prod-build-2024-06-17.json",
      "deployment": "prod",
      "action": "build",
      "status": "success"
    }
  ]
}
```

#### Examples

```bash
# Basic export (manifests only)
strata manifest export --out ./compliance

# Export with SBOMs for supply chain review
strata manifest export --out ./compliance --include-sbom

# Full export for compliance audit
strata manifest export --out ./compliance \
  --include-sbom \
  --include-platform

# Compress for archival
strata manifest export --out ./temp_compliance && \
  zip -r compliance-$(date +%Y-%m-%d).zip ./temp_compliance && \
  rm -rf ./temp_compliance
```

---

## Integration with `strata audit`

The `strata audit export` command now supports manifests:

```bash
# Export deploy logs plus manifests into one JSON file
strata audit export --include-manifests --out ./evidence.json

# Or newline-delimited JSON for log pipelines
strata audit export --include-manifests --format ndjson --out ./evidence.ndjson
```

---

## Common Workflows

### Audit Manifest Before Deploying

```bash
# 1. Build
strata build run -f deploy/prod.yaml

# 2. Inspect build manifest
strata manifest show .strata/build/prod/manifest.json

# 3. Check policy results
strata manifest show .strata/build/prod/manifest.json --output json | \
  jq '.spec.policy_results | select(.status != "passed")'

# 4. If satisfied, deploy
strata deploy run -f deploy/prod.yaml

# 5. Compare build vs. deploy
strata manifest show .strata/build/prod/manifest.json --output json > build.json
strata manifest list --deployment prod --last 1 --output json | \
  jq '.[0].path' | xargs strata manifest show --output json > deploy.json
diff <(jq '.spec.artifacts.repositories' build.json) \
     <(jq '.spec.artifacts.repositories' deploy.json)
```

### Create Compliance Evidence Package

```bash
# Gather all manifests, SBOMs, and configs
strata manifest export \
  --out ./compliance-evidence-2024-q2 \
  --include-sbom \
  --include-platform

# Submit to auditors
zip -r compliance-evidence-2024-q2.zip compliance-evidence-2024-q2/
email -to auditor@firm.com -file compliance-evidence-2024-q2.zip
```

### Query Manifest History

```bash
# Find all successful builds in the last 7 days
strata manifest list --output json | \
  jq '[.[] | select(.action == "build" and .status == "success") | .timestamp]' | \
  jq --arg cutoff "$(date -d '7 days ago' --iso-8601)" '.[] | select(. > $cutoff)'

# List users who deployed in production
strata manifest list --deployment prod --output json | \
  jq '[.[] | select(.action == "deploy")] | group_by(.user) | .[] | {user: .[0].user, count: length}'

# Find last build for rollback
strata manifest list --deployment prod --output json | \
  jq '[.[] | select(.action == "build")] | sort_by(.timestamp) | .[-1]'
```

---

## Troubleshooting

### No Manifests Found

**Cause:** `strata build run` or `strata deploy run` haven't been executed.

**Fix:**

```bash
# Build first
strata build run -f deploy.yaml

# List should now show manifests
strata manifest list
```

### Manifest File Not Readable

**Cause:** Permissions issue or corrupted file.

**Fix:**

```bash
# Check file exists and is readable
ls -lh .strata/build/prod/manifest.json

# Try reading as JSON
jq . .strata/build/prod/manifest.json
```

### Export Directory Not Created

**Cause:** Output directory doesn't exist or no write permissions.

**Fix:**

```bash
# Create directory first
mkdir -p ./compliance_package

# Export with explicit path
strata manifest export --out ./compliance_package
```

---

## Exit Codes

| Code | Meaning                                               |
| ---- | ----------------------------------------------------- |
| `0`  | Success                                               |
| `1`  | System error (file not found, permission denied)      |
| `2`  | Invalid arguments (missing required flag, bad format) |
| `3`  | Validation error (malformed manifest)                 |

---

## See Also

- [Deployment Manifests Guide](./deployment-manifests.md) — User guide for manifests
- [Audit & Compliance](./architecture.md#audit--compliance) — Compliance framework overview
- [ADR 0021: Manifests as First-Class Build Artifacts](../decisions/0021-deployment-manifests-as-first-class-build-artifacts.md) — Design rationale
