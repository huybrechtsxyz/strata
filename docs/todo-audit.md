# Audit & Traceability — Design Backlog

Target: prove to ISAE 3402 / ISO 27001 auditors **what changed, why, who approved, and how it was applied** — for every configuration change in a workspace repo (e.g. `xyz-configuration`).

---

## Why this matters

ISO 27001 (A.12.1.2 Change Management) and ISAE 3402 (Type II controls) both require:

| Requirement        | What the auditor asks                                 |
| ------------------ | ----------------------------------------------------- |
| What changed       | Which specific values were modified, before and after |
| Why it changed     | Business justification, change ticket reference       |
| Who approved       | Approver identity and date                            |
| How it was applied | Deployment mechanism, CLI version, exact commit SHA   |
| When it happened   | Timestamps correlated to deployment and system logs   |

Without this, you cannot pass a controls walkthrough for configuration changes.

---

## Three Layers — All Required

### Layer 1 — Process (PR template in `xyz-configuration`)

Lowest effort, highest auditor value. Every pull request that modifies a YAML config file must capture the *why* in structured form.

**What to build:** `.github/pull_request_template.md` in the configuration repo.

Required fields:
- Change ticket / work item reference (mandatory)
- What changed (free text — YAML paths, new vs old values)
- Why (business justification)
- Risk level (low / medium / high)
- Rollback plan
- Approver sign-off checklist (e.g. `- [ ] @infra-lead reviewed`)

**Status:** ⬜ Not implemented

---

### Layer 2 — Deployment Manifest (CLI — `xyz deploy run`)

When `xyz deploy run` executes, it writes an immutable JSON record to `.platform/deploy-log/`. This file is committed alongside the config change, creating a git-trackable deployment receipt.

**What to build:** After a successful (or failed) deploy, `RunDeployCommand` writes:

```json
{
  "execution_id": "...",
  "timestamp": "2026-05-19T14:32:00Z",
  "command": "deploy_run",
  "version": "0.0.1",
  "commit_sha": "abc123",
  "commit_message": "feat: increase replica count for production",
  "commit_author": "jane@example.com",
  "file": "deploy/deploy-prd.yaml",
  "stage": "production",
  "force": false,
  "dry_run": false,
  "success": true,
  "duration_seconds": 164,
  "steps": [
    { "step": "setup",  "success": true, "duration_seconds": 8  },
    { "step": "check",  "success": true, "duration_seconds": 2  },
    { "step": "plan",   "success": true, "duration_seconds": 45 },
    { "step": "apply",  "success": true, "duration_seconds": 109 }
  ],
  "errors": [],
  "messages": []
}
```

File path convention: `.platform/deploy-log/<ISO8601-timestamp>-<stage>.json`

This file should be committed to git by the CI pipeline immediately after deployment, creating an immutable audit trail in version control.

Implementation notes:
- Commit SHA and author come from `git rev-parse HEAD` / `git log -1 --format="%ae"` via the existing `GitIntegration`
- If git is unavailable (no `.git` dir), fields are `null` — not a failure
- The log directory itself should be `.gitignore`-free so CI can commit it

**Status:** ⬜ Not implemented  
**Files to change:** `src/xyz_platform/commands/deploy/run_deploy_command.py`, new `src/xyz_platform/utils/git_context.py` helper

---

### Layer 3 — `xyz audit changes` command (CLI)

On-demand report for auditors and engineers. Combines the deploy manifests (Layer 2) with `git log` on the config files.

**What to build:** New subcommand `xyz audit changes` with options:

```
xyz audit changes                        # list all deploy-log entries
xyz audit changes --since 2026-01-01     # filter by date
xyz audit changes --stage production     # filter by stage
xyz audit changes --last 10              # last N deployments
xyz audit changes --output json          # machine-readable for SIEM / audit tooling
```

Output includes per-deployment:
- Timestamp, stage, success, duration
- Commit SHA + message + author
- List of YAML files in the deploy path (from `git diff <prev_sha>..<this_sha> -- *.yaml`)
- Link to the deploy manifest JSON

Future: `xyz audit diff <sha1> <sha2>` — side-by-side diff of YAML values between two deployments.

**Status:** ⬜ Not implemented  
**Files to change:** New `src/xyz_platform/commands/audit/changes_audit_command.py`, update `src/xyz_platform/commands/cli_audit.py`

---

## Implementation Order

1. **Layer 1** (PR template) — no code, immediate value, do first
2. **Layer 2** (deployment manifest) — core evidence artifact, required before Layer 3
3. **Layer 3** (`xyz audit changes`) — surfaces the data, do last

---

## Open Questions

- Should deploy manifests also be pushed to an external immutable store (Azure Blob with immutability policy, Cosmos DB) in addition to git? Git is mutable by a repo admin — external storage provides stronger non-repudiation for ISAE.
- Should `xyz validate run` also write a validation manifest (same pattern) so we have evidence of pre-deploy checks?
- Access logs: who ran the CLI commands? Currently not captured. Consider logging `os.getlogin()` or the git committer identity into every execution record.
