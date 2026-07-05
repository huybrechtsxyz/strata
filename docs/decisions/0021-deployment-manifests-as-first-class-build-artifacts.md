# Deployment Manifests as First-Class Build Artifacts

- Status: accepted
- Date: 2026-07-05

## Summary

Deployment manifests are now generated during **both** `strata build run` (build-time) and `strata deploy run` (deploy-time), making them first-class artifacts in the build pipeline. This decision elevates manifests from deploy-only evidence to a comprehensive compliance and governance mechanism spanning the entire build → test → deploy → operate lifecycle.

**Key changes:**

1. Build manifests created automatically by `strata build run` (stored in `.strata/build/{deployment}/manifest.json`)
2. New `strata manifest` CLI command group for querying, displaying, and exporting manifests
3. Enhanced `strata audit export` with `--include-manifests` flag for compliance evidence packaging
4. Manifests capture build environment, policy results, SBOM references, and repository state
5. Both build and deploy manifests available for compliance, audit, and rollback workflows

---

## Problem

### Before

- **Manifests only at deploy time** — No evidence of what was built or validated before deployment
- **Build artifacts scattered** — SBOM, Terraform, Ansible, and other outputs not linked to a single immutable record
- **Compliance gaps** — No "gate" between build and deploy; no way to prove what was approved for deployment
- **No CLI for manifest operations** — Manifests were opaque JSON files; no `strata` commands to query them
- **Audit trail fragmentation** — Deploy logs separate from manifests; no integrated export for compliance

### Consequences

- Auditors ask "What exactly was deployed?" and you must manually reconstruct from scattered logs and files
- If a deployment fails, hard to correlate which build was attempted
- No way to enforce "only approved builds deploy" without external tooling
- Git-based audit trails lose correlation with the actual build/deploy process

---

## Solution

### 1. Build Manifests

`strata build run` now writes a manifest capturing:

- **Configuration snapshot** — Full `platform.json` with all resolved values
- **Repository state** — Git commits for all solution repositories
- **Build artifacts** — References to generated Terraform, Ansible, Helm, Compose files
- **Bill of Materials** — SBOM path and hash (CycloneDX JSON)
- **Policy results** — Any policy engine checks (enabled in future)
- **Build environment** — Timestamp, user identity, version, environment variables
- **Build duration** — How long the build took

**Location:**

```
.strata/build/{deployment}/manifest.json
```

**Action field:** `"action": "build"` (contrasts with `"action": "deploy"`)

### 2. Deploy Manifests Enhanced

`strata deploy run` now extends the build manifest with:

- **Stage results** — Per-stage status, duration, outputs (Terraform outputs, Ansible facts)
- **Provisioner details** — Terraform backend, Ansible inventory, Helm values
- **Container images** — References with digests
- **Deploy duration** — Total and per-stage timings
- **Deploy status** — Success or failure with error details

**Location:**

```
.strata/deployments/{deployment}/{version}/{timestamp}.json
```

**Action field:** `"action": "deploy"`

### 3. CLI Commands (`strata manifest`)

Three subcommands for manifest operations:

```bash
# List manifests from .strata/build/ and .strata/deployments/
strata manifest list [--deployment NAME] [--last N] [--output json]

# Show details of a single manifest (console or JSON)
strata manifest show <path> [--output json]

# Export manifests + optional SBOM + optional platform.json for compliance
strata manifest export --output-dir DIR [--include-sbom] [--include-platform]
```

### 4. Audit Export Enhancement

`strata audit export` now accepts `--include-manifests` flag:

```bash
strata audit export --output-dir ./evidence --include-manifests

# Creates:
# evidence/
#   deploy_logs/        ← existing deploy logs (unchanged)
#   manifests/          ← NEW: all deployment manifests
```

When flag is omitted, output format is unchanged (backward compatible).

---

## Design Rationale

### Why Generate at Build Time?

**Build manifests enable:**

1. **Pre-deploy review** — Inspect what was built before approval
2. **Policy enforcement** — Gating: "only builds that pass policy can deploy"
3. **Artifact traceability** — Link SBOM, Terraform plans, and config to exact commit/version
4. **Compliance gates** — Prove that build was reviewed and approved
5. **Rollback source** — Extract exact config from build manifest if rollback needed

### Why Both Build and Deploy?

**Build manifests** answer: "What did we intend to deploy?"

**Deploy manifests** answer: "What actually happened?"

Together, they provide:
- **Intent vs. reality** — Compare what was planned (build) vs. what succeeded (deploy)
- **Drift detection** — If build and deploy stages differ, something changed between them
- **Audit trail** — Two checkpoints: build approval and deploy execution

### Why CLI Commands?

Manifests are JSON files, but:
- Users shouldn't need to remember paths or JSON structure
- `strata manifest list` provides a unified view across build and deploy
- `strata manifest export` creates compliance evidence packages automatically
- Consistent with `strata audit`, `strata deploy`, `strata build` commands

### Why Store in `.strata/build/`?

Build manifests must be:
1. **Available after build completes** — Even if deploy never runs
2. **Separate from deploy manifests** — Clear distinction
3. **Accessible for review** — Before `strata deploy run` executes

Storing in `.strata/build/{deployment}/manifest.json` keeps them alongside other build outputs (platform.json, sbom.json, Terraform artifacts).

---

## Manifest Structure

### Build Manifest

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
    
    "artifacts": {
      "platform": {
        "hash": "sha256:abc123...",
        "path": ".strata/build/prod_deployment/platform.json",
        "content": {...}
      },
      "repositories": {...},
      "sbom": {...}
    },
    
    "policy_results": {
      "status": "passed",
      "policies_checked": 12,
      "policies_passed": 12
    }
  }
}
```

### Deploy Manifest

Deploy manifests extend build manifests with:

```json
{
  "spec": {
    "action": "deploy",
    "stages": [
      {
        "name": "infrastructure",
        "status": "success",
        "duration_seconds": 125,
        "outputs": {
          "server_ip": "192.0.2.10"
        }
      }
    ]
  }
}
```

---

## Implementation

### Changes to `strata build run`

1. After all builders complete (platform, terraform, ansible, compose, helm, sbom), write build manifest
2. Manifest includes full `platform.json` snapshot, repository state, SBOM reference, policy results
3. Stored at `.strata/build/{deployment}/manifest.json`
4. If `--dry-run`, skip manifest write

### New Command: `strata manifest`

- `manifest list` — Query `.strata/build/` and `.strata/deployments/`
- `manifest show` — Pretty-print manifest or JSON
- `manifest export` — Copy to output directory with optional SBOM/platform.json

### Changes to `strata audit export`

- Add `--include-manifests` flag
- When flag set, output includes `manifests/` subdirectory
- Backward compatible: flat array format when flag omitted

### Model Changes

- `DeploymentManifestModel.action` now accepts `"build" | "deploy" | "destroy"`
- Updated docstrings to document both build and deploy generation

---

## Compliance & Governance

### ISAE 3402 / NIS2 Alignment

**Build manifests provide:**
- `timestamp` — When build occurred (audit trail)
- `user` — Who initiated build (authorization trail)
- `artifacts.repositories[].commit` — Exact source code version (change control)
- `artifacts.platform.content` — Configuration snapshot (evidence)
- `policy_results` — Pre-deploy review results

**Deploy manifests provide:**
- `status` — Did deployment succeed? (execution evidence)
- `stages[].duration_seconds` — Timing for forensic analysis
- `stages[].outputs` — Resource IDs (infrastructure evidence)
- `artifacts.sbom` — Supply chain evidence

### Use Cases

1. **Change approval** — Deploy only if build manifest shows approved policy results
2. **Forensic analysis** — "What was deployed on date X?" → Extract from deploy manifest
3. **Supply chain** — "What dependencies were in production on date Y?" → Extract SBOM from manifest
4. **Rollback decision** — "What was in the last-known-good build?" → Restore from build manifest

---

## Trade-offs

### Pro

- ✅ Complete build → deploy audit trail in two manifests
- ✅ Enables build-time policy enforcement (future)
- ✅ Manifests available for review before deployment
- ✅ Unified CLI for manifest operations
- ✅ Supports compliance evidence export

### Con

- ❌ Additional disk space for full `platform.json` snapshot (mitigated by git storage)
- ❌ Manifest file size grows with config size (typically 100KB–1MB)
- ❌ Requires manifest configuration (local or GitOps) for deploy manifests

### Mitigation

1. **Storage options** — Local filesystem or GitOps (state repository) — choose based on compliance requirements
2. **Retention policy** — Implement cleanup jobs for old manifests (keep last 30 days by default)
3. **Lazy loading** — CLI commands fetch only metadata, not full `content` field unless requested

---

## Future Enhancements

Not included in this ADR, but enabled by this design:

1. **GPG signing** — Sign manifests with developer key (model already supports `signatures` field)
2. **Manifest diff** — `strata manifest diff m1 m2` — highlight changes between builds/deployments
3. **Policy engine** — `strata policy apply` — gates deployment based on policy results in manifest
4. **Manifest search** — `strata manifest query --user alice --after "2024-06-01"` — time-range queries
5. **Webhook integration** — Notify systems when new manifest created (compliance scanners, dashboards)

---

## Alternatives Considered

### Alt 1: Manifests Only at Deploy Time (Rejected)

**Rationale:** Loses build-time evidence. Can't prove what was built before deployment; can't gate on policy results.

### Alt 2: Store Build Manifest Inline in Platform.json (Rejected)

**Rationale:** Couples manifest to platform.json structure. Makes platform.json larger; harder to version separately.

### Alt 3: No CLI Commands, Manual JSON Queries (Rejected)

**Rationale:** Poor UX. Users shouldn't need to know JSON path to list manifests. `strata manifest` provides consistent interface.

### Alt 4: Manifests Only in GitOps Store (Rejected)

**Rationale:** Breaks offline workflows. Build manifests must be available locally after `strata build run`.

---

## References

- [Deployment Manifests Guide](../guides/deployment-manifests.md) — User-facing documentation
- [Configuration Schema → Manifest](../config/manifest.md) — Manifest storage configuration
- [Models → deployment-manifest](../platform/models.md) — YAML structure
- [Previous ADR: Deployment Audit Traceability (0018)](0018-deployment-audit-traceability.md) — Deploy-time manifest generation
