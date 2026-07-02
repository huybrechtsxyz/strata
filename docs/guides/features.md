# What strata Does — A Practical Overview

> **Audience:** DevOps engineer / sysadmin who lives in the terminal. You know Terraform, Ansible,
> and Helm. You've managed the copy-paste `.tfvars` sprawl. You want to know what strata actually
> does before deciding if it's worth your time.

strata is a CLI tool that sits in front of your IaC toolchain. It generates Terraform, Helm, and
Ansible inputs from layered YAML configuration, resolves secrets from external stores, and
orchestrates multi-stage deployments. The output is plain Terraform/Helm/Ansible — nothing
proprietary, no lock-in.

---

## Configuration as YAML layers, not copy-paste

Instead of duplicating `.tfvars` per environment, you write it once and override per environment:

```
workspaces/platform.yaml     ← what to build (providers, topology, modules)
environments/env-prd.yaml    ← production overrides (image versions, secrets, region)
environments/env-stg.yaml    ← staging overrides
deployments/deploy-prd.yaml  ← binds workspace + env, declares stages
deployments/deploy-stg.yaml
```

strata merges the layers at build time and generates the final Terraform/Helm inputs. No manual
diffing. No "which tfvars is the real one?"

### Cross-repo references

IaC can live in a separate repository. Reference it with `@repo_name/path`:

```yaml
provisioners:
  - name: platform_iac
    provisioner: terraform
    source:
      repository: xyz-infrastructure    # registered with `strata repo add`
      source_path: terraform/platform
```

strata resolves the path to the local clone at build time. Single-repo setups skip the `repository`
field and use a relative path directly.

---

## Secret resolution — never in git

Declare where a secret lives. strata fetches it at runtime and injects it into the build output.

**Supported secret backends:**

| Store            | Value field contains                         |
| ---------------- | -------------------------------------------- |
| `environment`    | Environment variable name on the runner      |
| `github`         | GitHub Actions secret name (runner-injected) |
| `azure-keyvault` | Secret name in Azure Key Vault               |
| `bitwarden`      | Bitwarden item ID                            |
| `vault`          | HashiCorp Vault path                         |
| `infisical`      | Infisical secret path                        |

```yaml
secrets:
  - key: DB_PASSWORD
    store: azure-keyvault
    value: prod-db-password          # secret name in Key Vault

  - key: API_TOKEN
    store: github
    value: MY_API_TOKEN              # GitHub Actions secret name
```

Secrets are resolved into memory, written to build output, and never logged. For Ansible SSH keys:
the PEM is written to a `chmod 600` temp file for the subprocess window, then deleted — even if the
playbook fails.

### Auto-generated secrets

For secrets that don't yet exist, declare a generator spec. strata creates the secret in the
backing store on first run:

```yaml
secrets:
  - key: SESSION_KEY
    store: azure-keyvault
    value: app-session-key
    generate:
      type: urlsafe
      length: 64
```

Supported generators: `urlsafe`, `hex`, `password`, `alphanumeric`, `numeric`, `base64`, `uuid4`,
`uuid7`. Minimum length: 8. Maximum: 1024.

---

## Variable stores for non-sensitive config

Variables (non-sensitive) support the same pluggable backend pattern:

| Store             | Resolves from                      |
| ----------------- | ---------------------------------- |
| `constant`        | Literal value in the YAML file     |
| `environment`     | Environment variable on the runner |
| `azure-appconfig` | Azure App Configuration key        |
| `consul`          | HashiCorp Consul key               |
| `vault`           | HashiCorp Vault path               |
| `infisical`       | Infisical secret path              |
| `etcd`            | etcd key                           |

Feature flags use `constant`, `environment`, `azure-appconfig`, or `flagsmith`.

---

## Build — generate your Terraform/Helm inputs

```bash
strata build run --file deploy/deploy-prd.yaml
```

Generates `.strata/build/deploy-prd/<stage>/` containing:
- `main.tf`, `variables.tf`, `terraform.tfvars.json` (Terraform stages)
- `values.yaml` (Helm stages)
- Populated `backend.tf` pointing to your existing state backend

Your state backend, your Terraform modules, your provider plugins — untouched. The output is plain
files that work without strata installed.

### Preview changes before you apply

```bash
strata build plan --file deploy/deploy-prd.yaml
```

Runs `terraform plan` in the generated output directory. Review the diff before committing to
`apply`. Useful as a PR gate — fail the pipeline on unexpected changes.

```bash
# Clean generated artifacts and start fresh
strata build clean --file deploy/deploy-prd.yaml
```

---

## Deploy — run the provisioner pipeline

```bash
strata deploy run   --file deploy/deploy-prd.yaml
strata deploy run   --file deploy/deploy-prd.yaml --stage infrastructure   # single stage
strata deploy run   --file deploy/deploy-prd.yaml --force                  # skip prompts
```

Orchestrates stages in dependency order. Each stage calls the provisioner via subprocess —
`terraform apply`, `ansible-playbook`, `helm upgrade`, `docker stack deploy`, or a script.

### Supported provisioners

| Provisioner | What it calls                       | Typical stage type        |
| ----------- | ----------------------------------- | ------------------------- |
| `terraform` | `terraform init/plan/apply`         | `infrastructure`          |
| `opentofu`  | `tofu init/plan/apply`              | `infrastructure`          |
| `ansible`   | `ansible-playbook`                  | `configure`, `initialize` |
| `helm`      | `helm upgrade --install`            | `deploy`                  |
| `compose`   | `docker stack deploy`               | `deploy`                  |
| `script`    | Shell / PowerShell / Python scripts | any                       |

### Other deploy subcommands

```bash
strata deploy show     --file deploy/deploy-prd.yaml   # inspect resolved configuration
strata env output      --file deploy/deploy-prd.yaml   # live infrastructure outputs
strata deploy history  --file deploy/deploy-prd.yaml   # deployment history
strata deploy health   --file deploy/deploy-prd.yaml   # run health checks
strata deploy output   --file deploy/deploy-prd.yaml   # print infrastructure outputs (cached)
strata deploy destroy  --file deploy/deploy-prd.yaml   # tear down (with confirmation)
strata deploy lock     --file deploy/deploy-prd.yaml   # acquire deployment lock
```

---

## Deployment locking

Before applying, strata can acquire a lease on your state backend to prevent concurrent deploys.
Set `locking: true` in your deployment YAML or pass `--lock` on the CLI. Backed by the same
mechanism as your Terraform backend (Azure Blob lease, S3 DynamoDB lock, etc.).

```bash
strata deploy lock    --file deploy/deploy-prd.yaml      # acquire lock
strata deploy run     --file deploy/deploy-prd.yaml      # fails if another lock is held
```

---

## Lifecycle hooks — run scripts at any phase

Attach shell, PowerShell, or Python scripts to any phase of the pipeline:

```yaml
# workspace.yaml
lifecycle:
  deploy_apply_before:
    scripts:
      - scripts/backup-state.ps1    # take a snapshot before applying
  deploy_provision:
    scripts:
      - scripts/notify-slack.sh     # post to Slack when provisioning starts
  deploy_output:
    scripts:
      - scripts/export-ips.ps1      # write IPs to a config file after deploy
```

Scripts receive context via environment variables (`STRATA_PHASE`, `STRATA_BUILD_PATH`,
`STRATA_WORKSPACE_PATH`, etc.). Works cross-platform — strata tries `.sh` on Linux, `.ps1` on
Windows, with fallbacks. Skip all hooks with `--no-hooks`.

---

## Policy guardrails

Declare compliance rules in a `configuration.yaml`. strata evaluates them at the appropriate
phase — no wrappers, no external policy server.

```yaml
spec:
  policies:
    - name: enforce_regions
      type: tenant_zone
      phase: plan
      enforcement: deny           # deny | warn | audit

    - name: require_tags
      type: required_tags
      phase: build
      enforcement: warn

    - name: naming_convention
      type: naming_pattern
      phase: validate
      enforcement: deny

    - name: checkov_scan
      type: script
      phase: plan
      enforcement: deny
      configuration:
        command: checkov --directory .strata/build/deploy-prd/infra
```

**Built-in policy types:**

| Type                      | Checks                                                   |
| ------------------------- | -------------------------------------------------------- |
| `tenant_zone`             | Terraform resource regions vs. allowed zones             |
| `required_tags`           | Required tags present on all resources                   |
| `naming_pattern`          | `meta.name` fields match a configured regex              |
| `script`                  | Delegates to any external command (OPA, Checkov, custom) |
| `sbom_pinned_versions`    | Container images use pinned (non-floating) tags          |
| `sbom_allowed_registries` | Images come only from approved registries                |
| `sbom_denied_packages`    | No dependency matches a purl/name blocklist              |
| `sbom_max_components`     | Total SBOM component count stays within a budget         |
| `sbom_license`            | Dependency licenses match an allow/deny list             |

---

## Schema validation on any YAML file

```bash
strata validate --file workspaces/platform.yaml
```

Returns exit code `3` on schema violations, with structured error output:

```json
{
  "success": false,
  "errors": [
    { "code": "VALIDATION_ERROR", "message": "Field 'spec.provider' is required", "line": 12 }
  ]
}
```

Use as a pre-commit hook or PR gate. Fast — no external tools required.

---

## Inspect resolved values before you deploy

Check exactly what strata will inject into the build — variables, secrets, feature flags — without
running the full build:

```bash
strata values list --file deploy/deploy-prd.yaml
strata values get  --file deploy/deploy-prd.yaml --key DB_PASSWORD
```

Secrets are masked in output unless you pass `--reveal`. Useful for debugging "why is the wrong
value being used?"

---

## Tool health check

```bash
strata tools status             # show all external tools and their versions
strata tools status --missing   # show only tools not on PATH
strata tools check              # exit non-zero if any required tool is absent
strata tools install            # attempt to install missing tools
```

Covers: Terraform, OpenTofu, Ansible, Docker, Helm, Azure CLI, kubectl, Bitwarden CLI, Vault CLI,
and more.

---

## Secret utilities

```bash
# Generate a cryptographically secure secret value (without storing it anywhere)
strata secret generate --type urlsafe --length 64
strata secret generate --type uuid4
strata secret generate --type password --length 32

# Mask a secret value in a string (replaces it with ***)
strata secret mask "my text containing secretvalue123"
```

Useful for seeding Key Vault or Bitwarden from a setup script.

---

## CI-friendly by design

**JSON output everywhere:**

```bash
strata build plan --file deploy/deploy-prd.yaml --output json
```

```json
{ "success": true, "data": { "changes": 3, "additions": 1, "deletions": 2 }, "errors": [] }
```

**Environment variable config:**

```bash
export STRATA_FILE=deploy/deploy-prd.yaml
export STRATA_OUTPUT=json
export STRATA_WORK_PATH=/workspace

strata build plan    # no flags needed
strata deploy run
```

**Exit codes:**

| Code | Meaning                             |
| ---- | ----------------------------------- |
| `0`  | Success                             |
| `1`  | Execution failure / crash           |
| `2`  | Usage error (bad flags)             |
| `3`  | Validation failure (schema-invalid) |

**Shell completions:**

```bash
strata completion bash   >> ~/.bashrc
strata completion zsh    >> ~/.zshrc
strata completion fish   >> ~/.config/fish/completions/strata.fish
strata completion powershell >> $PROFILE
```

---

## Dev container out of the box

`strata sln init` creates a `.devcontainer/` in your workspace with Python 3.13, Terraform, Azure
CLI, kubectl, and Helm pre-installed. Open in VS Code → **Reopen in Container**, or push to GitHub
and open as a Codespace — no local tool setup required.

---

## Workspace templates

Seed a new workspace with a standard structure in one command:

```bash
strata sln init --name my-platform --template .strata/templates/three-repo-standard.yaml
```

Templates declare which repos to clone, which profiles to create, and which files to link. Skip the
manual Phase 1–4 setup for standard layouts.

---

## Create config files from templates

```bash
strata new workspace
strata new environment
strata new deployment
strata new provider
strata new resource
```

Scaffolds a valid YAML skeleton for the chosen kind. Faster than looking up the schema.

---

## Inspect JSON schemas

```bash
strata schema list              # list all supported YAML kinds
strata schema get workspace     # print the full JSON schema for kind: workspace
```

Feed the schema into your editor's YAML plugin for inline validation and autocompletion.

---

## Persistent CLI defaults

```bash
strata config set output json           # always use JSON output
strata config set verbose true          # always show verbose logs
strata config set file deploy/prd.yaml  # default deployment file
strata config list                      # verify what's set
strata config unset verbose             # remove a default
```

Stored in `.strata/cli.yaml` — per-workspace, committed to git so the team shares the same
defaults.

---

## Extend strata without touching core code

Drop Python files into your workspace's `.strata/` directory. strata auto-discovers and loads them
at startup — no version upgrade, no pull request to the core repo required.

### Custom integrations

Add support for a new secret store, variable backend, or external tool by dropping a `.py` file
into `.strata/integrations/`:

```
.strata/
  integrations/
    my_vault.py       ← loaded automatically at startup
```

Each file's `register()` function inserts a `type → class` mapping into `IntegrationFactory`.
Any YAML entry whose `type:` matches the registered string gets instantiated through the factory
from that point on:

```yaml
# In your workspace or configuration YAML
integrations:
  - type: my_vault
    spec:
      endpoint: https://vault.example.com
```

Declare **capability protocols** to signal what your integration can do — the same interfaces the
built-in integrations use:

| Protocol              | What it enables                                        |
| --------------------- | ------------------------------------------------------ |
| `ISecretStore`        | Read secrets by key (plugs into secret resolution)     |
| `IVariableStore`      | Read key/value config (plugs into variable resolution) |
| `IFeatureFlagStore`   | Read feature flags                                     |
| `IRepositoryTool`     | Clone / fetch / push (git-style)                       |
| `IInfrastructureTool` | Plan / apply (Terraform-style)                         |
| `IContainerTool`      | Build / run (Docker-style)                             |

A starter template is scaffolded at `.strata/integrations/my_integration.py` by `strata sln init`.

### Custom policies

Enforce team-specific compliance rules by dropping a `.py` file into `.strata/policies/`:

```
.strata/
  policies/
    require_cost_tags.py    ← loaded automatically at startup
```

The file's `register()` call adds the type to the policy engine registry. Reference it in any
`configuration.yaml` the same way as a built-in type:

```yaml
spec:
  policies:
    - name: require_cost_tags
      type: require_cost_tags    # matches your registered TYPE_NAME
      phase: plan
      enforcement: deny
      configuration:
        required_tags: [CostCenter, Owner, Environment]
```

`evaluate()` receives a `PolicyContext` containing the current phase, workspace path, resolved
artifact, and (for the plan phase) the raw `terraform show -json` output. Return a `PolicyResult`
with pass/fail and structured violations.

A starter template is scaffolded at `.strata/policies/my_policy.py` by `strata sln init`.

---

## The escape hatch

If strata stops fitting your workflow:

1. Run `strata build run` one last time.
2. Copy `.strata/build/<deployment>/<stage>/` to wherever you keep Terraform code.
3. `terraform init && terraform plan` — works without strata, no proprietary files.

Your state backend is unchanged. Your modules are unchanged. Your infrastructure is unchanged.
strata never touches `.tfstate`.
