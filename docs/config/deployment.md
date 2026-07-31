# Deployment Configuration

Top-level orchestration files combining workspace definitions, environments, and configurations to create **actual infrastructure instances**. A deployment represents a concrete, deployable unit.

## Conceptual Model

| Layer           | Purpose          | Description                                          |
| --------------- | ---------------- | ---------------------------------------------------- |
| **Workspace**   | WHAT to build    | Infrastructure blueprint                             |
| **Environment** | HOW to customize | Environment-specific overrides                       |
| **Deployment**  | ACTUAL INSTANCE  | Combines workspace + environment(s) + configurations |

**Deployment = Workspace + Environment(s) + Configuration(s) + Orchestration Controls**

## Schema

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: <deployment_name>      # Required: ^[a-z][a-z0-9_]*$
  annotations:
    description: <description>
  labels:
    version: "<version>"
spec:
  partial: false               # Optional: reusable base fragment, not directly deployable
  extends: <@repo/path.yaml>   # Optional: inherit from another deployment file
  tenant: <tenant_code>        # Optional: tenant ownership reference
  layers: {}                   # Optional: layer values
  properties: {}               # Optional deployment metadata
  custom: {}                   # Optional organizational metadata
  workspace:                   # Required for non-partial deployments
    name: <workspace_name>
    file: <path/to/workspace.yaml>
    description: <description>
  environments: []             # Optional list of env file refs (applied in order)
    - <path/to/environment.yaml>
    - file: <path/to/environment.yaml>
      scope: shared
  configurations: []           # Optional additional config refs
    - name: <configuration_name>
      file: <path/to/configuration.yaml>
      description: <description>
  versions: []                 # Optional version file refs (applied in list order)
    - <path/to/version-or-lock.yaml>
  locking: {}                  # Optional concurrent deploy protection
  promotion: {}                # Optional promotion wave assignment
  stages: []                   # Optional staged execution graph
  gates: []                    # Optional hand-off gates
  lifecycle: {}                # Optional deploy lifecycle hooks
```

## Properties & Custom

**Properties** - Deployment identification:

```yaml
properties:
  tenant: acme-corp
  project: platform
  environment: production
```

**Custom** - Organizational metadata:

```yaml
custom:
  owner: "Platform Team"
  costcenter: "PROD-001"
  billing_code: "BC-123"
```

## Workspace Reference

Required reference to infrastructure blueprint:

```yaml
workspace:
  name: platform_workspace
  file: config/workspaces/platform.yaml
  description: Platform workspace blueprint
```

## Environments

Environment configs applied in order (later overrides earlier):

```yaml
environments:
  - config/environments/production.yaml
  - file: config/environments/us-east.yaml # Overrides production
    scope: regional
```

**Multiple environments enable layered configuration composition.**

## Configurations

Additional configuration layers (optional):

```yaml
configurations:
  - name: tenant_config
    file: config/configurations/tenant-a.yaml
```

_Use for: application-specific settings, tenant configs, compliance requirements_

## Versions, Gates, and Stage Secrets

**Versions** are optional deployment-level references used by the version resolver:

```yaml
versions:
  - versions/prd.manifest.yaml
  - versions/prd.yaml
```

**Gates** are optional deployment-level hand-off requirements before selected stages.

**Stage secrets** are declared per stage (`spec.stages[].secrets`) and control which
sensitive values may be injected into that stage.

Deployment-level `features`, `variables`, and `secrets` are not part of the current
deployment schema. Define those in environment files instead.

## Configuration Merge Order

Precedence from lowest to highest:

1. Workspace defaults (base)
2. Environment files in listed order (later files override earlier ones)
3. Deployment orchestration fields (`stages`, `gates`, `locking`, `promotion`, `versions`)

Use environment files for variable/secret/feature data layering. Use deployment spec
for orchestration and references.

To trace which file contributed each resolved value:

```bash
strata values list -f deploy/deploy-prd.yaml --trace
```

See [Environment Composition](../guides/environment-composition.md) for patterns and full examples.

## Examples

**Simple:**

```yaml
meta:
  name: platform_prod
  labels:
    version: "1.0.0"
spec:
  properties:
    tenant: acme-corp
    environment: production
  workspace:
    name: platform_workspace
    file: config/workspaces/platform.yaml
  environments:
    - config/environments/production.yaml
```

**Multi-Layer:**

```yaml
meta:
  name: tenant_deployment
  labels:
    version: "2.0.0"
spec:
  properties:
    tenant: tenant-a
    project: saas-platform
  custom:
    tenant_id: "CUST-001"
    tier: "premium"
    sla: "99.99%"
  workspace:
    name: saas_workspace
    file: "@config/workspaces/saas-platform.yaml"
  environments:
    - "@config/environments/production.yaml"
    - file: "@config/environments/us-east.yaml"
      scope: regional
  configurations:
    - name: tenant_a_config
      file: "@config/configurations/tenant-a.yaml"
  versions:
    - versions/prd.manifest.yaml
    - versions/prd.yaml
```

**GitOps:**

```yaml
meta:
  name: gitops_deployment
spec:
  workspace:
    name: infrastructure
    file: "@infra/workspaces/main.yaml"
  environments:
    - "@env/environments/production/us-east-1.yaml"
```

## Use Cases

**Multi-tenant SaaS:**

```text
workspace: saas-platform.yaml (same for all)
deployments/
├── tenant-a-deployment.yaml  # Premium tier
├── tenant-b-deployment.yaml  # Standard tier
└── tenant-c-deployment.yaml  # Enterprise tier
```

**Multi-region:**

```text
workspace: global-platform.yaml (same)
deployments/
├── us-east-deployment.yaml     # US East region
├── eu-west-deployment.yaml     # EU West region
└── ap-south-deployment.yaml    # Asia Pacific
```

**Blue-green:**

```yaml
# Blue (current)
blue-deployment.yaml:
  versions:
    - versions/blue.manifest.yaml
    - versions/blue.yaml

# Green (new version)
green-deployment.yaml:
  versions:
    - versions/green.manifest.yaml
    - versions/green.yaml
```

**Staged rollout:**

```yaml
# Canary (1%)
canary-deployment.yaml:
  environments:
    - environments/production.yaml
    - environments/canary-1pct.yaml

# Beta (20%)
beta-deployment.yaml:
  environments:
    - environments/production.yaml
    - environments/beta-20pct.yaml

# Full (100%)
production-deployment.yaml:
  environments:
    - environments/production.yaml
```

## Deployment Workflow

1. Load deployment → Parse config
2. Resolve file references → Load workspace/environment/config files
3. Merge environment layers → Apply list-order overrides
4. Validate → Validate merged config
5. Generate artifacts → Create Terraform/manifests/Helm values
6. Execute lifecycle → Run workspace phases
7. Provision infrastructure → Deploy infrastructure
8. Deploy applications → Deploy namespaces/modules
9. Verify → Run health checks
10. Register → Register deployment instance

## Stages

Deployment stages are the unit of execution. Each stage maps to exactly one deployer run (one IaC tool, one lifecycle context). Stages execute sequentially in declaration order.

```yaml
stages:
  - name: network          # Unique label within the deployment
    provisioner: my_tf     # Name of a provisioner in workspace.spec.provisioners
    # topology: k8s_aks   # Alternative to provisioner — resolved via topology map
    # scope: infra        # Optional label for --scope CLI filtering (see deploy run --scope)
    secrets:               # Allowlist of secrets this stage may access (default-deny)
      - hetzner_api_token
    timeouts:              # Per-step overrides (seconds)
      setup: 120
      plan: 600
      apply: 3600
```

### `provisioner` vs `topology`

Exactly one of `provisioner` or `topology` must be set (or omitted when the workspace has a single provisioner and the deployer can infer it).

| Field         | Behaviour                                                                                                                                                                                                                                                 |
| ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `provisioner` | Matches a provisioner entry in `workspace.spec.provisioners` by **name**. Direct, explicit.                                                                                                                                                               |
| `topology`    | Looks up a topology entry in `workspace.spec.topology` by name, reads its `provisioner` type, then finds the matching provisioner. Use when stages should be expressed in logical terms (e.g. "kubernetes") rather than tooling names (e.g. "my_aks_tf"). |

If both are set, validation fails. If neither is set and the workspace has a single provisioner, that one is used as a runtime fallback.

### `scope` — stage filtering

`scope` is an optional free-form label. Assign the same label to stages that belong to the same logical group, then use `--scope <label>` on the CLI to run only that group:

```bash
strata deploy run -f deploy/deploy-prd.yaml --scope infra   # only stages with scope: infra
strata deploy run -f deploy/deploy-prd.yaml --scope apps    # only stages with scope: apps
strata deploy run -f deploy/deploy-prd.yaml                 # all stages (no scope filter)
```

Stages without a `scope` field are skipped when `--scope` is set. This is intentional — unlabelled stages are treated as "always run" only when no filter is active.

---

## Cross-Stage Outputs

After a stage's `apply` step completes, the deployer collects its outputs and makes them available to all **subsequent** stages in the same deployment run. This requires zero YAML configuration — the pipeline handles injection automatically.

### STRATA_CONTEXT and STRATA_SENSITIVE

Two runtime dictionaries flow through the deployment pipeline:

| Name                 | Contents                                           | Injected into subprocesses                 | Logged |
| -------------------- | -------------------------------------------------- | ------------------------------------------ | ------ |
| **STRATA_CONTEXT**   | variables + features + non-sensitive stage outputs | Yes (`TF_VAR_*`, `--extra-vars`, env vars) | Yes    |
| **STRATA_SENSITIVE** | secrets + sensitive stage outputs                  | Only keys declared per stage               | Never  |

**STRATA_CONTEXT** is seeded with environment variables and features before the first stage runs. After each stage completes, its non-sensitive outputs are added. Every subsequent stage receives the full accumulated context automatically.

**STRATA_SENSITIVE** is seeded with secrets before the first stage runs. Sensitive stage outputs accumulate after each stage. Access is **default-deny** — a stage receives only the secret keys it declares in its `secrets` allowlist.

### Stage secret scoping

Each stage declares which secrets it may access. This is a security measure — stages only see what they need.

```yaml
stages:
  - name: provision
    provisioner: terraform_hetzner
    secrets:
      - hetzner_api_token        # Only this secret is visible

  - name: configure
    topology: hetzner_servers
    secrets:
      - ssh_private_key          # Only SSH key visible, not the API token

  - name: verify
    provisioner: script_healthcheck
    # No secrets declared → no sensitive values available
```

| `secrets` value      | Behaviour                                              |
| -------------------- | ------------------------------------------------------ |
| Omitted / `null`     | No secrets — default-deny                              |
| `[]` (empty list)    | No secrets — explicit deny                             |
| `["key_a", "key_b"]` | Only those keys from secrets + sensitive stage outputs |
| `["*"]`              | All secrets + all sensitive outputs (escape hatch)     |

### How it works

1. After `apply`, `RunDeployCommand` calls `deployer.collect_outputs()` on the just-completed stage.
2. Non-sensitive outputs are stored in `ResolvedValues.stage_outputs` and injected into every subsequent stage via `TF_VAR_<key>` (Terraform) or bare `<KEY>` (Compose).
3. Sensitive outputs are stored in `ResolvedValues.stage_outputs_sensitive` and filtered by the next stage's `secrets` allowlist before injection.

### Sensitive output handling

Sensitivity is determined by the IaC tool, not by YAML configuration.

**Terraform** — reads the `sensitive` flag from `terraform output -json`:

```hcl
output "cluster_endpoint" {
  value     = azurerm_kubernetes_cluster.main.kube_config[0].host
  sensitive = false  # → injected as TF_VAR_cluster_endpoint in downstream stages
}

output "kubeconfig" {
  value     = azurerm_kubernetes_cluster.main.kube_config_raw
  sensitive = true   # → held internally, never injected
}
```

**Other deployers** — use underscore-prefix convention: keys starting with `_` are treated as sensitive.

### Injection format

| Deployer type        | Injection format               | Example                               |
| -------------------- | ------------------------------ | ------------------------------------- |
| Terraform / OpenTofu | `TF_VAR_<key>` env var         | `TF_VAR_cluster_endpoint=https://...` |
| Docker Compose       | Bare key env var + `.env` file | `CLUSTER_ENDPOINT=https://...`        |

Dictionaries and lists are JSON-encoded before injection.

### Console output

```
✓  Collected 3 output(s), 1 sensitive (not injected) for downstream stages.
```

---

## Provisioner Stage Types

Stages reference a provisioner by name. The backend tool used by that provisioner determines which deployer executes the stage.

| Type        | Deployer            | Notes                                                                                                                                              |
| ----------- | ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `terraform` | `TerraformDeployer` | Requires `terraform` CLI                                                                                                                           |
| `opentofu`  | `TerraformDeployer` | Requires `tofu` CLI                                                                                                                                |
| `ansible`   | `AnsibleDeployer`   | Requires `ansible-playbook` CLI                                                                                                                    |
| `compose`   | `ComposeDeployer`   | Requires Docker with Swarm mode; deploys per-namespace `docker-compose.yml` files. See [ComposeDeployer](../platform/deployers.md#composedeployer) |
| `helm`      | `HelmDeployer`      | Requires `helm` CLI; deploys per-module Helm releases. See [HelmDeployer](../platform/deployers.md#helmdeployer)                                   |
| `script`    | `ScriptDeployer`    | Executes lifecycle scripts; no external CLI required                                                                                               |

## Deploying ArgoCD ApplicationSets

ArgoCD ApplicationSets are Kubernetes CRDs — raw YAML manifests, not Helm values.
Strata's Helm deployer passes `values.yaml` and `--set` flags to `helm upgrade`; it
does not apply raw manifest files. This means ApplicationSets cannot be deployed as
standalone YAML files through a strata Helm stage.

**Recommended pattern: embed ApplicationSets as Helm values**

The ArgoCD Helm chart exposes `server.additionalApplications` (and the newer
`extraObjects` in chart v6+) which accepts Kubernetes resource definitions as plain
YAML under a Helm value. Pass your ApplicationSet definition there and Helm renders
it as part of the ArgoCD chart installation — no separate `kubectl apply` step needed.

```yaml
# workspaces/infrastructure.yaml
spec:
  provisioners:
    - name: argocd
      provisioner: helm
      source:
        repository: argocd_charts
        source_path: argocd
```

```yaml
# modules/argocd/values.yaml  (in argocd_charts repo)
server:
  additionalApplications: []   # extended per-environment via overrides

# ── OR with chart v6+ extraObjects ──────────────────────────────────────
extraObjects:
  - apiVersion: argoproj.io/v1alpha1
    kind: ApplicationSet
    metadata:
      name: customer-webapp
      namespace: argocd
    spec:
      generators:
        - list:
            elements: []        # populated via environment override
      template:
        spec:
          source:
            repoURL: https://git.company.com/deploy-charts
            chart: company-webapp
            targetRevision: "3.2.1"
          destination:
            server: https://kubernetes.default.svc
            namespace: "{{namespace}}"
```

Environment overrides inject the per-environment generator list:

```yaml
# environments/production.yaml
spec:
  overrides:
    modules:
      - module: argocd
        configuration:
          extraObjects:
            - apiVersion: argoproj.io/v1alpha1
              kind: ApplicationSet
              metadata:
                name: customer-webapp
                namespace: argocd
              spec:
                generators:
                  - list:
                      elements:
                        - code: acme
                          namespace: acme-prod
                        - code: contoso
                          namespace: contoso-prod
                template:
                  spec:
                    source:
                      chart: company-webapp
                      targetRevision: "3.2.1"
                    destination:
                      namespace: "{{namespace}}"
```

**Why this is the right approach:**

- No strata change required — strata passes values to Helm, Helm renders the CRD
- The ApplicationSet definition is version-controlled in your chart values / overrides
- Per-environment generator lists are managed through the standard environment override mechanism
- ArgoCD owns the application lifecycle; strata owns the infrastructure lifecycle

**Alternatives if you need raw manifest apply:**

- Use a `script` provisioner that runs `kubectl apply -f` as a lifecycle hook
- Apply ApplicationSets directly via ArgoCD's own GitOps sync (no strata involvement)
- Wait for a future `kubectl` deployer type (tracked as a potential future enhancement)



**Local refs:** Relative paths like `config/workspaces/platform.yaml`  
**Cross-repo refs:** `@repo_name/path/to/file.yaml` (resolved from configured remotes)

## Best Practices

- **Naming:** Use pattern `<tenant>_<environment>_<region>`
- **Version control:** Track deployment files
- **Immutable references:** Use specific versions for workspace/environment
- **Layered config:** Environments for reusable, configurations for one-offs
- **Secret management:** Use appropriate secret managers
- **Feature flags:** Keep feature toggles in environment files for controlled rollouts
- **Documentation:** Document purpose in annotations
- **Validation:** Validate before applying
- **Testing:** Test in lower environments first
- **Rollback plan:** Maintain previous configs

## Locking

Optionally protect the deploy pipeline from concurrent runs. When enabled, strata acquires a lock before the first stage and releases it in a `finally` block — covering hooks, Terraform, Ansible, health checks, and policy evaluation.

The lock backend is derived automatically from the provisioner's `backend.type` — no separate connection configuration is needed.

```yaml
spec:
  locking:
    enabled: true          # false by default
    strategy: wrap         # wrap | delegate  (default: wrap)
    wait_timeout: 30m      # how long to wait for a held lock (default: 30m)
    force_unlock_after: 8h # stale lock TTL — auto-release after this (default: 8h)
```

| Field                | Type                  | Default | Description                                                                           |
| -------------------- | --------------------- | ------- | ------------------------------------------------------------------------------------- |
| `enabled`            | bool                  | `false` | Activate locking for this deployment                                                  |
| `strategy`           | `wrap` \| `delegate`  | `wrap`  | `wrap` = strata holds the lock; `delegate` = trust TFC run queue (TFC-only pipelines) |
| `wait_timeout`       | duration (e.g. `30m`) | `30m`   | How long `deploy run` waits for a held lock before aborting with exit code 3          |
| `force_unlock_after` | duration (e.g. `8h`)  | `8h`    | *(Phase 3)* Auto-release stale locks older than this threshold                        |

`--dry-run` always skips lock acquisition regardless of this setting.

### Lock Backend by Provisioner Type

The backend is selected from the first Terraform provisioner's `backend.type`. Stages with no backend (Ansible, scripts) fall back to a local file lock.

| `backend.type`    | Lock mechanism                       | Multi-machine | Requires                    |
| ----------------- | ------------------------------------ | ------------- | --------------------------- |
| `azurerm`         | Azure Blob infinite lease            | Yes           | `az` CLI + Storage access   |
| `terraform_cloud` | TFC Workspace Lock API               | Yes           | `TF_TOKEN_app_terraform_io` |
| `remote`          | TFC Workspace Lock API (alias)       | Yes           | `TF_TOKEN_app_terraform_io` |
| `consul`          | Consul session + KV acquire          | Yes           | `CONSUL_HTTP_TOKEN` (opt.)  |
| `s3`              | *(Phase 3)* DynamoDB conditional put | Yes           | `boto3` + AWS credentials   |
| `gcs`             | *(Phase 3)* GCS object conditions    | Yes           | `google-cloud-storage`      |
| `local` / none    | File lock (`fcntl` / `msvcrt`)       | No            | None                        |

### Example — Azure Blob lock

```yaml
spec:
  locking:
    enabled: true
    wait_timeout: 15m
  # Lock backend derived from the first provisioner's backend:
  # workspace.spec.provisioners[*].backend.type: azurerm
  # (storage_account_name + container_name read from backend.configuration)
```

### Example — Terraform Cloud lock

```yaml
spec:
  locking:
    enabled: true
    wait_timeout: 30m
  # Lock backend derived from:
  # workspace.spec.provisioners[*].backend.type: terraform_cloud
  # organization + workspaces.name read from backend.configuration
  # Token from TF_TOKEN_app_terraform_io env var or ~/.terraformrc
```

### Example — Consul lock

```yaml
spec:
  locking:
    enabled: true
    wait_timeout: 10m
  # Lock backend derived from:
  # workspace.spec.provisioners[*].backend.type: consul
  # address read from backend.configuration (default: http://127.0.0.1:8500)
  # Token from CONSUL_HTTP_TOKEN env var
```

### Managing locks manually

```bash
# Inspect current state
strata deploy lock status -f deployment.yaml

# Release a stuck lock (your own)
strata deploy lock release -f deployment.yaml

# Force-release another holder's lock
strata deploy lock release -f deployment.yaml --force

# View recent lock history
strata deploy lock history -f deployment.yaml --last 20
```

---

## Validation

Platform validates:

- Valid deployment name (lowercase, alphanumeric, underscores)
- Required workspace reference
- Valid file references (`workspace.file`, `environments[*].file`, `configurations[*].file`)
- Unique stage/gate/configuration names where declared
- Gate scope references only declared stage names
- Stage dependency graph has valid references and no cycles

## Troubleshooting

**Validation failed:** Check workspace/environment/configuration file references and stage/gate validation errors  
**File resolution failed:** Verify local paths and `@repo/path` references resolve under current workspace/remotes  
**Merge conflicts:** Review environment list order and section-level overrides in environment files  
**Version pin mismatch:** Validate `spec.versions` files exist and hashes are current when lock files are used  
**Instance not unique:** Ensure unique deployment name and review conflicting metadata

## Summary

Deployments create **concrete infrastructure instances** by combining workspace (blueprint) + environment(s) (settings) + configuration(s) (overrides). Enables:

- Multi-layer configuration composition
- Flexible deployment strategies (blue-green, canary, staged)
- Consistent, repeatable provisioning across customers/regions/environments
- Single source of truth with appropriate customizations