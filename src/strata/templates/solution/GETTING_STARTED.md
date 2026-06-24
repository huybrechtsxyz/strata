# Getting Started — {{ SOLUTION_NAME }}

This guide walks you through the full lifecycle of a Strata workspace: configure, validate, build, and deploy.

---

## Workspace structure

```
.strata/          Strata state (solution.json, schemas, logs) — managed automatically
config/           Global platform configuration (providers, integrations, repositories)
stack/            Reusable building blocks (workspaces, namespaces, modules)
envs/             Environment variable and secret files (one per environment)
deploy/           Deployment descriptors (one per environment, links stack + envs)
```

If you initialised with a template, the `config/`, `stack/`, `envs/`, and `deploy/` folders
are already populated. If not, run `strata sln init --template aks` (or another template)
to scaffold them.

---

## First-time setup

### 1. Register your config repository

Strata resolves `@repo/path` references from named repositories. Register this directory
(or a remote git clone) as your config source:

```bash
# Local directory (path relative to where you run strata)
strata repo add {{ SOLUTION_NAME }} . --type local

# — OR — a remote git repository (cloned into the workspace)
strata repo add {{ SOLUTION_NAME }} <git-url> --branch main --clone
```

### 2. Create a profile and point it at your config files

A profile links an environment name to its config and environment files:

```bash
strata profile add prd --activate
strata ref config add {{ SOLUTION_NAME }}-config \
    --path "@{{ SOLUTION_NAME }}/config/{{ SOLUTION_NAME }}-config.yaml"
strata ref env add {{ SOLUTION_NAME }}-env \
    --path "@{{ SOLUTION_NAME }}/envs/env-prd.yaml"
```

### 3. Check workspace status

```bash
strata sln status
```

---

## Day-to-day lifecycle

### Validate — lint and check all YAML files

```bash
strata validate --file deploy/deploy-prd.yaml
```

Runs structural and cross-reference checks on the deployment and all files it pulls in.

### Build — generate deployment artifacts

```bash
strata build run --file deploy/deploy-prd.yaml
```

Produces a build artifact (e.g. rendered Helm values, Terraform state, compose files) in `.strata/build/`.

### Deploy — provision and apply

```bash
strata deploy run --file deploy/deploy-prd.yaml
```

Runs the provisioner (Terraform, Helm, Compose, …) for each stage defined in the deployment.

### Destroy — tear down

```bash
strata deploy destroy --file deploy/deploy-prd.yaml
```

---

## Common commands

| Task               | Command                           |
| ------------------ | --------------------------------- |
| Workspace overview | `strata sln status`               |
| List repositories  | `strata repo list`                |
| List profiles      | `strata profile list`             |
| Show config values | `strata config list`              |
| Show resolved vars | `strata vars list`                |
| Validate a file    | `strata validate --file <path>`   |
| Build              | `strata build run --file <path>`  |
| Deploy             | `strata deploy run --file <path>` |
| View recent logs   | `strata log show`                 |
| CLI help           | `strata --help`                   |

---

## Next steps

- Edit `config/{{ SOLUTION_NAME }}-config.yaml` to add your provider credentials and repository settings.
- Edit `envs/env-prd.yaml` to declare variables, secrets, and feature flags for the `prd` environment.
- Run `strata validate --file deploy/deploy-prd.yaml` to confirm everything is wired up correctly.
- See the template-specific `README.md` for provisioner-specific prerequisites and notes.
