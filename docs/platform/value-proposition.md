# Value Proposition

## What strata does

- **Generates deployment artifacts from YAML configuration** — workspaces, modules, variables, providers, and backends composed into ready-to-apply files for your provisioner of choice.
- **Resolves secrets at build time** — pulls values from Key Vault, Bitwarden, or environment variables and injects them into build output without ever committing secrets to Git.
- **Merges configuration across environments** — one set of YAML definitions, multiple environments (dev/staging/prod) with layered overrides.
- **Orchestrates deployment stages** — runs provisioners in the correct order across stages, with drift detection (`strata diff`) built in.

> **Terraform is the primary example throughout this document, but the same architecture applies to any provisioner strata supports — Helm, Ansible, Pulumi, or others.** The build/deploy pipeline is provisioner-agnostic: strata generates artifacts and calls the tool via subprocess. Swap the provisioner, keep the workflow.

## How it helps

### Config sprawl

Infrastructure teams copy-paste `.tfvars` files between environments, creating drift and inconsistency. strata eliminates this with a single source of truth in YAML, merged per-environment at build time.

### Secret management

Hardcoded secrets in variable files or CI scripts are a compliance liability. strata resolves secrets from pluggable backends (Azure Key Vault, Bitwarden, HashiCorp Vault) and writes them into the build output only — never into source control.

### Environment drift

When environments diverge silently, production surprises follow. `strata diff` compares the current build output against the last-applied state, surfacing drift before it becomes an incident.

### Deployment orchestration

Multi-stage deployments (infra before apps, networking before compute) require manual ordering or brittle scripts. strata's deployment manifests declare stage order and execute them sequentially with proper dependency tracking.

## What strata owns vs. what it doesn't

| Aspect                   | strata owns                                                                   | strata does NOT touch                                     |
| ------------------------ | ----------------------------------------------------------------------------- | --------------------------------------------------------- |
| **Configuration source** | YAML files (`workspace`, `environment`, `deployment`, `configuration`)        | Your Terraform modules (`.tf` files you wrote)            |
| **Build output**         | `.strata/build/<deployment>/<stage>/` — generated `.tf`, `.tfvars.json`       | `.terraform/` directory (provider cache, plugin binaries) |
| **State**                | Nothing — strata generates a `backend.tf` that points to *your* state backend | `terraform.tfstate`, remote state in Azure/S3/GCS         |
| **Execution**            | Calls `terraform init/plan/apply` via subprocess                              | No custom providers, no proprietary API calls             |
| **Workspace metadata**   | `.strata/solution.json`, `.strata/cli.yaml`, build logs                       | Git history, CI/CD pipelines, infrastructure itself       |

## The Escape Hatch

strata does not generate Terraform state. It does not use custom providers. It does not wrap Terraform in a proprietary runtime. The build output is plain Terraform — files you can read, copy, and run without strata installed.

### Where the generated Terraform lives

```
strata build run --file deploy/deploy-prd.yaml
```

After this command, your Terraform is at:

```
.strata/build/deploy-prd/
├── infra/
│   ├── main.tf
│   ├── variables.tf
│   ├── terraform.tfvars.json
│   ├── providers.tf
│   └── backend.tf
└── apps/
    ├── main.tf
    ├── variables.tf
    ├── terraform.tfvars.json
    ├── providers.tf
    └── backend.tf
```

### Walking away

If strata doesn't fit your workflow, here's how to leave:

1. Run `strata build run` one last time to generate current artifacts.
2. Copy `.strata/build/<deployment>/<stage>/` to wherever you keep Terraform code.
3. Run `terraform init && terraform plan` in that directory. It works — there's nothing strata-specific in those files.
4. Delete `.strata/` if you like. Your state backend, your modules, your infrastructure — all untouched.

Your Terraform state was never inside strata. It lived in your configured backend (Azure Storage, S3, GCS, Terraform Cloud) the entire time. Removing strata doesn't orphan state or require migration.

### No lock-in by design

- **No custom providers** — standard `hashicorp/azurerm`, `hashicorp/aws`, Helm charts, Ansible playbooks — whatever your provisioner uses natively.
- **No proprietary state format** — state is standard Terraform state (or Helm release history, Ansible facts) in your backend.
- **No runtime dependency** — the build output is self-contained. `terraform apply`, `helm upgrade`, `ansible-playbook` — they work without strata installed.
- **No phone-home** — strata is a local CLI tool with no SaaS component.
- **Provisioner-agnostic** — Terraform is the most common target, but Helm and Ansible follow the same build → deploy → diff lifecycle. The architecture doesn't assume a single tool.

We want you to stay because strata saves you time and reduces errors — not because leaving is hard.
