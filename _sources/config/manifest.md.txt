# Deployment Manifest Configuration

Control how deployment manifests are written after successful or failed `strata deploy run` commands. Deployment manifests capture audit-ready evidence: Git commits, version tags, timestamps, user, complete configuration, infrastructure state, and SBOM data.

## Purpose

When enabled, deployment manifests provide:

- **Compliance audit trail** — Exact infrastructure state at deploy time (NIS2, ISAE 3402 Type 2)
- **Deployment history** — Track what was deployed, when, by whom, and what changed
- **Rollback reference** — Pin exact versions for recovery
- **Forensic analysis** — Investigate drift or failed deployments

When the `manifest` section is omitted from configuration entirely, manifests are not written and a log message is emitted.

## Schema

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: <name>
spec:
  manifest:
    path: <path>                # Base directory. Default: ".strata/deployments"
    push_manifest: true | false # Commit and push the manifest to this workspace's own git repo
    repository:                 # Optional — durable push to a named solution repo instead (ADR-0065)
      push: true | false        # Whether to push at all
      name: <repo_name>         # Name of a repo registered with `strata repo add`; omit to push to this workspace's own repo
      path: <path_in_repo>      # Where inside that repo the manifest lands; defaults to "manifest"
```

The manifest is **always written locally** at `{path}/{deployment_name}/{version}/{timestamp}.json` when the `manifest` section is present — this part never depends on any push configuration. `push_manifest`/`repository` only control whether that same file is *additionally* committed and pushed to git afterwards.

## Local Storage (always on when `manifest` is configured)

```yaml
manifest:
  path: ".strata/deployments"
```

**Output structure:**

```text
.strata/deployments/
  my_deployment/
    v1.0.0/
      2024-06-15T14:32:45Z.json
    v1.0.1/
      2024-06-16T09:15:22Z.json
```

**Path resolution:**

The service auto-appends `/{deployment_name}/{version}/{timestamp}.json` to the base path:

```text
{path}/{deployment_name}/{version}/{timestamp}.json
```

This is the only thing `path` controls. There is no separate "local" vs. "gitops" storage mode — every manifest is written to disk the same way; git push is a separate, optional step layered on top (below).

---

## Durable git-push (ADR-0065 Phase 1)

Additionally commit and push the manifest file to git after writing it locally. Two destinations are available:

**Push to this workspace's own repo** — the simple case, unchanged from previous behaviour:

```yaml
manifest:
  path: ".strata/deployments"
  push_manifest: true
```

This commits and pushes from the current workspace's own git checkout, to its own `origin` remote.

**Push to a named solution repo instead** — for a shared state repo used across multiple workspaces:

```yaml
manifest:
  path: ".strata/deployments"
  repository:
    push: true
    name: state-repo        # must be registered with `strata repo add`, not spec.remotes
    path: history/manifest  # where inside that repo — optional, defaults to "manifest"
```

`repository.name` resolves against the **solution-level repo registry** (`strata repo add`, listed in `solution.json`) — the same registry `@repo_name/path` cross-repo file references use elsewhere. It is a different, separate concept from `spec.remotes` (`RemoteModel` — named remote endpoints used for `gitops` provisioner backends and `ref_convention` policy tag conventions); a repo referenced here must be registered via `strata repo add`, not declared under `spec.remotes`.

When multiple workspaces push manifests to the same `repository.name`, the workspace name is automatically inserted as a path segment (`{repository.path}/{workspace}/...`) to prevent them from overwriting each other's history — this happens unconditionally and needs no configuration.

**Output structure (pushed to a named repo):**

```text
state-repo/
  history/manifest/
    my_workspace/
      my_deployment/
        v1.0.0/
          2024-06-15T14:32:45Z.json
```

**Use a named repo when:**

- Multiple teams or workspaces need shared deployment history in one place
- Compliance requires an immutable audit trail in version control separate from the deployment repo itself
- Automated downstream processes consume manifests (e.g., compliance scanners) from one predictable location

---

## Manifest Content

Every manifest captures:

| Field                           | Contents                                       | Purpose                                      |
| ------------------------------- | ---------------------------------------------- | -------------------------------------------- |
| **deployment_name**             | Name of the deployment                         | Identification                               |
| **workspace_name**              | Referenced workspace name                      | Traceability                                 |
| **action**                      | `build`, `deploy`, or `destroy`                | Operation type                               |
| **status**                      | `success`, `partial`, or `failed`              | Outcome                                      |
| **started_at**/**completed_at** | ISO 8601 timestamps (UTC)                      | When it happened                             |
| **deployed_by**                 | Actor identity                                 | Who deployed it                              |
| **artifacts.platform**          | Hash + full `platform.json` content            | Complete configuration snapshot              |
| **artifacts.repositories**      | Pinned commits for all referenced repos        | Version control proof                        |
| **artifacts.images**            | Container images with digests (if applicable)  | Application versions                         |
| **artifacts.providers**         | All provisioners, backends, and state backends | Infrastructure tooling                       |
| **sbom**                        | Reference to the generated CycloneDX SBOM file | Component inventory & vulnerability tracking |
| **policy_results**              | Policy evaluation results from all phases      | Governance evidence                          |
| **lock**                        | State-lock audit trail (if locking enabled)    | Concurrency audit trail                      |
| **audit_log**                   | Path to this execution's deploy-log entry      | Cross-reference to the execution narrative   |
| **stages**                      | Per-stage status, duration, outputs            | Execution details                            |

## Examples

### Local only — Development

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: dev_manifest
spec:
  manifest:
    path: ".strata/deployments"
```

Run:

```bash
strata deploy run -f deployments/dev.yaml
# Writes: .strata/deployments/dev/0.1.0/2024-06-15T14:32:45Z.json
```

### Local + durable push to a shared repo — Production

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: prod_manifest
spec:
  manifest:
    path: "deployments"
    repository:
      push: true
      name: xyz-state-repo   # registered via `strata repo add xyz-state-repo <url>`
      path: history/manifest
```

Run:

```bash
strata deploy run -f deployments/prod.yaml
# Writes locally: deployments/prod/2.3.0/2024-06-17T10:45:33Z.json
# Pushes to: xyz-state-repo/history/manifest/<workspace>/prod/2.3.0/2024-06-17T10:45:33Z.json
```

---

## Troubleshooting

| Issue                        | Cause                                | Solution                                                         |
| ---------------------------- | ------------------------------------ | ---------------------------------------------------------------- |
| Manifest not written         | `manifest` section missing in config | Add a `manifest` section with at least `path`                    |
| Push fails silently          | `repository.name` not registered     | Verify the name matches a repo registered with `strata repo add` |
| Manifest has empty `content` | Platform artifact not generated      | Run `strata build run` before `strata deploy run`                |

---

## Integration with Deployment Configuration

Manifests are **not** defined in the deployment YAML itself — they are controlled by the **configuration** file that the deployment uses. This allows:

- Reuse of the same deployment manifest config across multiple deployments
- Central audit policy (one configuration file per environment or team)
- Flexible storage switching without rewriting deployment files

**Typical layout:**

```text
config/
  configurations/
    prod_manifest.yaml          <- defines manifest storage
  deployments/
    prod_deployment.yaml        <- references the configuration
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
