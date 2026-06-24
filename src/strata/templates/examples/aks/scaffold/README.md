# {{ solution_name }}

Configuration repository managed by [strata](https://github.com/huybrechtsxyz/strata).

> Generated from the **aks** built-in template.

---

## What's here

| Folder    | Purpose                                                           |
| --------- | ----------------------------------------------------------------- |
| `config/` | Global platform configuration — providers, integrations, layering |
| `stack/`  | Reusable building blocks — workspaces, namespaces, modules        |
| `deploy/` | Environment deployment descriptors (one per environment)          |
| `envs/`   | Environment variable files (secrets resolved at runtime)          |

---

## First-time setup

```bash
# 1. Register this repo as the config source
strata repo add {{ solution_name }} <git-url> --branch main --clone

# 2. Add an environment profile and activate it
strata profile add prd --activate
strata ref config add {{ solution_name }}-config --path "@{{ solution_name }}/config/{{ solution_name }}-config.yaml"
strata ref env add {{ solution_name }}-env --path "@{{ solution_name }}/envs/env-prd.yaml"

# 3. Validate
strata validate --file deploy/deploy-prd.yaml

# 4. Deploy
strata deploy run --file deploy/deploy-prd.yaml
```

---

## Prerequisites

| Tool                                                                       | Why                      |
| -------------------------------------------------------------------------- | ------------------------ |
| [Terraform](https://developer.hashicorp.com/terraform/install) 1.6+        | AKS cluster provisioning |
| [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) | Azure authentication     |
| [kubectl](https://kubernetes.io/docs/tasks/tools/)                         | AKS cluster management   |
| [helm](https://helm.sh/docs/intro/install/)                                | Helm chart deployment    |
