# Configuration FAQ

Common questions about structuring and writing strata YAML configuration files.

---

## What is the difference between a workspace and a deployment?

They operate at different levels of abstraction:

| File kind     | Answers         | Contains                                              |
| ------------- | --------------- | ----------------------------------------------------- |
| `workspace`   | What to build   | Providers, provisioners, topology, namespaces         |
| `environment` | How to adapt it | Variable/secret overrides, module pins, feature flags |
| `deployment`  | What to run     | References to workspace + environment(s) + configs    |

A **workspace** is an infrastructure blueprint — it is reusable across environments. A **deployment** is a concrete runnable instance that binds a workspace to one or more environments and adds any deployment-level overrides.

The typical layout for one application across two environments:

```
workspaces/
  platform.yaml          ← kind: workspace (shared)
environments/
  env-prd.yaml           ← kind: environment
  env-stg.yaml           ← kind: environment
deployments/
  deploy-prd.yaml        ← kind: deployment → refs platform + env-prd
  deploy-stg.yaml        ← kind: deployment → refs platform + env-stg
```

---

## Do I need an environment file, or can I put everything in the deployment?

Both work. Environment files are optional — variables and secrets can go directly in the deployment:

```yaml
# Deployment-only approach (fine for one-off deployments)
spec:
  workspace:
    name: platform_workspace
    source:
      type: local
  variables:
    - key: DATACENTER
      store: constant
      value: kamatera-eu-fr
  secrets:
    - key: KAMATERA_API_KEY
      store: env
      value: KAMATERA_API_KEY
```

Use a separate **environment file** when:
- Multiple deployments share the same environment-level settings (staging creds, staging-specific variables).
- You want a clear audit boundary between "what the workspace defines" and "how staging differs from production."
- You use `overrides` to pin module image versions or disable features per environment.

---

## What is the difference between variables and secrets?

| Aspect      | `variables`                              | `secrets`                                  |
| ----------- | ---------------------------------------- | ------------------------------------------ |
| Sensitivity | Non-sensitive (hostnames, counts, flags) | Sensitive (API keys, passwords, tokens)    |
| Storage     | Plain YAML, committed to git             | Resolved at runtime from an external store |
| In logs     | May appear in build output               | Never logged — memory-only                 |

**Variable stores** (`spec.variables[].store`):

| Store             | Resolves from                      |
| ----------------- | ---------------------------------- |
| `constant`        | Literal value in the YAML file     |
| `environment`     | Environment variable on the runner |
| `azure-appconfig` | Azure App Configuration key        |
| `consul`          | HashiCorp Consul key               |
| `vault`           | HashiCorp Vault path               |
| `infisical`       | Infisical secret path              |
| `etcd`            | etcd key                           |

**Secret stores** (`spec.secrets[].store`):

| Store            | Resolves from                                      |
| ---------------- | -------------------------------------------------- |
| `constant`       | Literal value in YAML — avoid for real secrets     |
| `environment`    | Environment variable on the runner                 |
| `github`         | GitHub Actions secret (env var injected by runner) |
| `azure-keyvault` | Azure Key Vault secret                             |
| `bitwarden`      | Bitwarden item (by item ID)                        |
| `vault`          | HashiCorp Vault path                               |
| `infisical`      | Infisical secret path                              |

```yaml
variables:
  - key: REPLICA_COUNT
    store: constant
    value: "3"

secrets:
  - key: DB_PASSWORD
    store: bitwarden
    value: a1b2c3d4-0000-0000-0000-000000000000   # Bitwarden item ID
```

If a value would be embarrassing to see in a CI log, it belongs in `secrets`.

---

## How do variables layer across workspace, environment, and deployment?

Later layers override earlier ones for the same key. The merge order is:

```
workspace variables
  ↓ (overridden by)
environment variables   (applied in order if multiple environments are listed)
  ↓ (overridden by)
deployment variables    (highest precedence)
```

The same rule applies to secrets. This means you can set sensible defaults in the workspace and only override the values that differ per environment:

```yaml
# workspace: sets REPLICA_COUNT = 1 as a baseline
# env-prd: overrides REPLICA_COUNT = 3
# deploy-prd: no override needed — inherits 3 from the environment
```

---

## What does `meta.name` allow?

`meta.name` is a `PlatformName` — it follows the pattern `^[a-z][a-z0-9_-]*$`:

- Starts with a lowercase letter
- Only lowercase letters, digits, underscores, and hyphens
- No uppercase, no spaces
- 1–64 characters

```yaml
meta:
  name: platform_workspace   # ✓ underscores allowed
  name: platform-workspace   # ✓ hyphens allowed
  name: PlatformWorkspace    # ✗ uppercase not allowed
  name: 1_workspace          # ✗ must start with a letter
```

File names on disk can be chosen freely — the restriction is only on `meta.name` inside the file.

---

## What is the difference between `properties`, `custom`, and `labels`?

All three are optional metadata fields but serve different purposes:

| Field        | Purpose                                    | Who reads it          |
| ------------ | ------------------------------------------ | --------------------- |
| `properties` | Tool-specific settings strata acts on      | strata internals      |
| `custom`     | Free-form organizational metadata          | Your tooling / humans |
| `labels`     | Document-level tags (version, environment) | Filtering, display    |

```yaml
meta:
  labels:
    version: "1.0.0"           # document version
    environment: production    # used for filtering

spec:
  properties:
    terraform:
      organization: my-tfc-org   # strata reads this for Terraform Cloud backend
      cloudspace: production

  custom:
    owner: Platform Team         # free-form — strata ignores these
    costcenter: PROD-INFRA-001
    compliance: PCI-DSS
```

Put values that strata needs to function in `properties`; put values your organisation cares about but strata ignores in `custom`.

---

## When do I need a `configuration` file?

A `configuration` file (`kind: configuration`) defines validation rules and defaults for providers, resources, and topologies. It is not required for a deployment to run, but it provides:

- **Allowed region lists** — reject deployments targeting unsupported regions.
- **Resource validation** — enforce CPU/RAM combinations with regex patterns.
- **Topology rules** — set min/max counts per role (e.g., Docker Swarm managers must be odd, 1–7).

```yaml
kind: configuration
spec:
  providers:
    - name: kamatera
      additional_regions: false     # only allow the listed regions
      regions: [eu-fr, us-ny]
      additional_resources: false   # only allow the listed resource types
      resources:
        - name: virtualmachine
          configuration:
            cpu_cores: "^[1-9][0-9]?$"        # 1–99
            ram_mb: "^(512|1024|2048|4096)$"   # fixed sizes only
  topologies:
    - type: docker-swarm
      components:
        - role: manager
          min_count: 1
          max_count: 7
```

A deployment references a configuration file the same way it references an environment — through the `configurations` list. Multiple configuration layers merge in order.

---

## How many provisioners does a workspace need?

At minimum one — typically a `terraform` provisioner for infrastructure. Add an `ansible` provisioner when you need OS-level configuration after infrastructure is up:

```yaml
provisioners:
  - name: platform_iac
    provisioner: terraform
    source:
      source_path: terraform
    backend:
      type: azurerm
      configuration:
        resource_group_name: rg-terraform-state
        storage_account_name: satfstate
        container_name: tfstate
        key: platform.tfstate

  - name: platform_config
    provisioner: ansible
    source:
      source_path: ansible
    configuration:
      playbook: site.yml
      ssh_private_key_secret: platform_ssh_key
```

Provisioners are referenced by name in topology components and deployment stages. A workspace can define multiple provisioners of the same type (e.g., two separate Terraform root modules).

---

## How do I share a provider file across multiple workspaces?

Reference it via a cross-repo path using `@repo_name/` notation:

```text
# workspace A
providers:
  - name: kamatera_eu
    file: @platform-config/providers/kamatera-eu-fr.yaml

# workspace B — reuses the same provider file
providers:
  - name: kamatera_eu
    file: @platform-config/providers/kamatera-eu-fr.yaml
```

`platform-config` must be a repository registered in the solution (`strata repo add`). The `@repo_name/` prefix is resolved to the local clone path at build time.

For single-repo setups, use a relative path without the `@` prefix:

```yaml
providers:
  - name: kamatera_eu
    file: config/providers/kamatera-eu-fr.yaml
```

---

## How do environment overrides work?

`spec.overrides` in an environment file lets you customize workspace resources, modules, and providers without touching the shared workspace definition.

**Pin container image versions per environment:**

```yaml
spec:
  overrides:
    modules:
      - module: xyz_backend
        services:
          - name: server
            image: registry.example.com/backend:2025.3.0
          - name: worker
            image: registry.example.com/backend:2025.3.0
```

**Enable or disable a module in a specific environment:**

```yaml
spec:
  overrides:
    modules:
      - module: debug_dashboard
        enabled: false   # disable in production
```

**Change a provider region:**

```yaml
spec:
  overrides:
    providers:
      - name: kamatera_eu
        properties:
          region: us-ny   # staging deploys to a different region
```

Overrides are additive and non-destructive — they only change the fields you specify, leaving everything else from the workspace definition intact.

---

## Can the same workspace be deployed to multiple environments simultaneously?

Yes — that is the intended pattern. Create one deployment file per environment, each pointing to the same workspace:

```yaml
# deploy-prd.yaml
spec:
  workspace:
    name: platform_workspace
    source:
      type: local
  environments:
    - name: haven_env_prd
      source:
        type: local
```

```yaml
# deploy-stg.yaml
spec:
  workspace:
    name: platform_workspace   # same workspace
    source:
      type: local
  environments:
    - name: haven_env_stg      # different environment
      source:
        type: local
```

Each deployment builds into its own output directory and uses its own Terraform state key — they are completely independent at runtime.

---

## How do I provide an SSH key for Ansible without putting it in the YAML?

Store the private key in your secret store (Bitwarden, HashiCorp Vault, Azure Key Vault, etc.) and reference it by name in `configuration.ssh_private_key_secret`. The key never appears in any YAML file — it is resolved into memory at deploy time and written to a `chmod 600` temp file that is deleted when the step completes.

**1. Store the key in Bitwarden (or any other supported store):**

Keep the full PEM content (from `-----BEGIN OPENSSH PRIVATE KEY-----` to `-----END OPENSSH PRIVATE KEY-----`) as a secure note or password field in your secret store. Note the item ID.

**2. Reference it in the workspace provisioner:**

```yaml
spec:
  provisioners:
    - name: haven_init
      provisioner: ansible
      source:
        repository: haven
        source_path: ansible
      configuration:
        playbook: site.yml
        ssh_private_key_secret: haven_ssh_key   # name used to look up the key at runtime
```

And in your environment or deployment YAML, declare the secret source:

```yaml
spec:
  secrets:
    - key: haven_ssh_key
      store: bitwarden
      value: b2f90a12-3c4d-5678-abcd-ef1234567890   # Bitwarden item ID
```

**3. What happens at deploy time:**

```
strata deploy run
  └─ resolves secrets from Bitwarden into memory (ResolvedValues)
  └─ AnsibleDeployer looks up resolved_values.secrets["haven_ssh_key"]
  └─ writes PEM to /tmp/ansible_ssh_XXXXX.pem  (chmod 600)
  └─ ansible-playbook site.yml --private-key /tmp/ansible_ssh_XXXXX.pem
  └─ temp file deleted (even if the playbook fails)
```

The key is never written to disk except during the subprocess window, and never logged.

**Host key checking:** Because the runner has no prior trust with freshly provisioned VMs, set `ANSIBLE_HOST_KEY_CHECKING=false` in your CI environment (or in `ansible.cfg`). For fixed infrastructure with stable IPs, populate `~/.ssh/known_hosts` via `ssh-keyscan` and remove the env var.

---

## How do I adopt strata with existing Terraform state?

strata generates standard Terraform files that point to your existing state backend — it never creates, manages, or migrates state.

1. **Create your strata workspace YAML** describing the infrastructure you already have.
2. **Point the backend config** to your existing state backend (same storage account, same key).
3. **Run `strata build run`** to generate Terraform files.
4. **Run `strata build plan`** (or `terraform plan` directly in `.strata/build/`):
   - If the plan shows no changes → your YAML accurately describes reality. Done.
   - If the plan shows changes → adjust your YAML until the plan is clean.

Your Terraform state stays exactly where it is. strata just generates the `.tf` files that Terraform reads — it never touches `.tfstate`.

**What about `terraform import`?**

If you have infrastructure that was created outside Terraform (manually, or via another tool), you'll still need `terraform import` to bring it under state management. strata doesn't change this workflow — after import, your state reflects reality, and strata generates the config that matches.

---

## How do I configure multiple stages in a deployment?

Define each stage in `spec.stages`, referencing a named provisioner from the workspace. Use `depends_on` to control execution order.

```yaml
spec:
  stages:
    # Stage 1 — provision infrastructure with Terraform
    - name: infrastructure
      provisioner: platform_iac       # must match a name in workspace spec.provisioners
      scope: all
      on_failure: stop

    # Stage 2 — configure VMs with Ansible after provisioning
    - name: configuration
      provisioner: platform_config    # must match a name in workspace spec.provisioners
      scope: all
      depends_on: [infrastructure]
      on_failure: stop
```

`provisioner` must match a `name` defined in `spec.provisioners` of the referenced workspace YAML. strata orchestrates the stages in dependency order — each stage calls its provisioner via subprocess and receives only the secrets it is explicitly allowed to access via `stage.secrets`.

Alternatively, use `topology` instead of `provisioner` to scope a stage to a specific workspace topology entry, which derives the provisioner automatically and limits execution to that topology's resources.

---

## How do I roll back a deployment?

The model is declarative — there is no imperative rollback command. Revert the YAML change in git and redeploy:

```bash
git revert <commit-that-broke-things>
git push

strata build run --file deploy/deploy-prd.yaml
strata deploy run --file deploy/deploy-prd.yaml
```

Run `strata build plan --file deploy/deploy-prd.yaml` first to review exactly what Terraform will change before applying. The plan shows resources reverting to their previous state and is auditable before any change is applied.
