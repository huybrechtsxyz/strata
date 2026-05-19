# strata

strata is a **modular, multi-repository infrastructure platform** for managing workspaces and cluster orchestration. It uses a separation-of-concerns architecture where configuration, modules, and deployments live in separate version-controlled repositories, enabling repeatable, audit-ready infrastructure deployments.

All infrastructure is defined in YAML. The CLI (`xyz`) orchestrates the full lifecycle — from workspace initialization through build artifact generation and Terraform provisioning — without manual scripting.

## Table of Contents

- [strata](#strata)
  - [Table of Contents](#table-of-contents)
  - [Key Features](#key-features)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [Quick Start](#quick-start)
  - [Configuration](#configuration)
  - [CLI Reference](#cli-reference)
  - [Deployment Workflow](#deployment-workflow)
  - [Testing](#testing)
  - [Troubleshooting](#troubleshooting)
  - [Contributing](#contributing)
  - [Security](#security)
  - [License](#license)
  - [Acknowledgments](#acknowledgments)
  - [Contact](#contact)
  - [Glossary](#glossary)
  - [Core Concepts](#core-concepts)
    - [Separation of Concerns](#separation-of-concerns)
    - [Version Control Everything](#version-control-everything)
    - [Declarative Configuration](#declarative-configuration)
    - [Audit Trail](#audit-trail)


---

## Key Features

- **Multi-repository architecture** — Platform code, modules, configuration, and deployments in separate repos; each independently versioned and tagged.
- **Declarative YAML configuration** — All infrastructure defined in Kubernetes-style YAML (`apiVersion`, `kind`, `meta`, `spec`). No imperative scripts.
- **Two-phase validation** — Phase 1: Pydantic schema checks. Phase 2: cross-repo reference resolution and integration credential checks.
- **Profile management** — Named profiles group environment-specific file references (config, environment, secrets). Switch between `dev`, `staging`, `prod` without touching YAML.
- **Audit-ready deployment manifests** — Every build captures exact Git commits, version tags, timestamps, user, and resource configuration — ready for NIS2 / ISAE 3402 evidence packages.
- **Pluggable secret backends** — Bitwarden, HashiCorp Vault, Azure Key Vault, Azure App Config, and environment variables all supported through a unified integration layer.
- **Terraform orchestration** — Build generates `.tfvars.json` and `platform.json` artifacts; deploy runs `terraform init → validate → plan → apply` per stage in the correct order.

---

## Prerequisites

| Tool                                                   | Version | Required for                       |
| ------------------------------------------------------ | ------- | ---------------------------------- |
| Python                                                 | 3.13+   | CLI runtime                        |
| [uv](https://docs.astral.sh/uv/)                       | latest  | Package and environment management |
| Git                                                    | any     | Repo management (`xyz repo sync`)  |
| [Terraform](https://developer.hashicorp.com/terraform) | 1.5+    | `xyz build` and `xyz deploy` only  |

---

## Installation

```bash
uv sync
```

**Linux / macOS:**
```bash
source .venv/bin/activate
xyz --help
```

**Windows:**
```powershell
.venv\Scripts\Activate.ps1
xyz --help
```

Or invoke directly without activating:
```bash
uv run strata --help
```

---

## Quick Start

```bash
# 1. Initialize a new workspace
cd /path/to/my-workspace
xyz init --name my-workspace
# Opens in VS Code? Select "Reopen in Container" to use the pre-configured dev container.

# 2. Register external repositories
xyz repo add xyz-config         git@github.com:org/xyz-config.git         --branch main --clone
xyz repo add xyz-infrastructure git@github.com:org/xyz-infrastructure.git --branch main --clone

# 3. Create an environment profile
xyz profile add prd --activate

# 4. Add file references to the active profile
xyz ref config add global-config --path "@xyz-config/config/xyz-config.yaml"
xyz ref env    add prd-env       --path "@xyz-config/environments/xyz-env-prd.yaml"

# 5. Validate a YAML file
xyz validate repos/xyz-config/config/xyz-config.yaml

# 6. Inspect resolved values before building
xyz values list -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml

# 7. Build deployment artifacts
xyz build run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml

# 8. Deploy
xyz deploy run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --dry-run
xyz deploy run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml
```

> **Tip:** Persist output format and verbosity once so you don't repeat flags:
> ```bash
> xyz config set output console
> xyz config set verbose true
> ```

---

## Configuration

Platform YAML files follow a Kubernetes-style schema:

```yaml
apiVersion: platform.huybrechts.xyz/v1
kind: workspace          # workspace | deployment | environment | provider | resource | ...
meta:
  name: my-workspace
  annotations:
    description: Production infrastructure workspace
spec:
  ...
```

Full schema documentation: [config/](config/readme.md)

Supported kinds: `configuration`, `workspace`, `deployment`, `environment`, `provider`, `resource`, `firewall`, `module`, `namespace`, `workspace-template`.

---

## CLI Reference

```
xyz <command> [options]
```

Standard options accepted by every command:

| Option                         | Description                                                               |
| ------------------------------ | ------------------------------------------------------------------------- |
| `--work-path PATH`             | Workspace root (or `STRATA_WORK_PATH` env var; walks up from CWD if not set) |
| `--output console\|text\|json` | Output format (default: `console`)                                        |
| `--verbose`                    | Show structured log output                                                |
| `--quiet`                      | Suppress all output                                                       |

Full command reference: [platform/commands.md](platform/commands.md)

---

## Deployment Workflow

The full lifecycle from workspace setup to running infrastructure:

1. `xyz init` — create workspace, optionally from a template
2. `xyz repo add / sync` — register and clone external repos
3. `xyz profile add / activate` — create named environment profiles
4. `xyz ref config / env add` — attach config and environment files to the active profile
5. `xyz validate` — validate individual YAML files
6. `xyz values list` — inspect resolved variables, secrets, and feature flags
7. `xyz build run` — generate `.tfvars.json`, `platform.json`, rendered templates
8. `xyz deploy run` — execute Terraform provisioners per stage

Full guide: [platform/workflow.md](platform/workflow.md)

---

## Testing

```bash
# Run full test suite
uv run pytest tests/ --no-cov -q

# Run with coverage
uv run pytest tests/ --cov=strata --cov-report=term-missing
```

For linting, type checking, and the full nox pipeline, see [CONTRIBUTING.md](../.github/CONTRIBUTING.md#developer-setup).

---

## Troubleshooting

```bash
# Built-in help topics
xyz help --list
xyz help --topic troubleshooting
xyz help --topic quickstart
```

Common issues:

| Symptom                              | Likely cause                      | Fix                                  |
| ------------------------------------ | --------------------------------- | ------------------------------------ |
| `Not inside an xyz workspace`        | CWD not in a workspace tree       | Run `xyz init` or pass `--work-path` |
| Exit 2 on any command                | Missing required option           | Check `xyz <command> --help`         |
| Exit 3 on `xyz validate`             | Schema-invalid YAML               | Read the validation error output     |
| `@repo-name/...` reference not found | Repo not registered or not cloned | `xyz repo add` + `xyz repo sync`     |
| Terraform not found                  | `terraform` not on PATH           | Install Terraform 1.5+               |

---

## Contributing

Contributions are welcome! See [CONTRIBUTING.md](../.github/CONTRIBUTING.md) for workflow guidelines and developer setup (linting, testing, nox, lockfiles).

---

## Security

See [SECURITY.md](../.github/SECURITY.md) for the vulnerability reporting policy.

---

## License

This project is licensed under the GNU Affero General Public License v3.0 (AGPL-3.0). See the [LICENSE](../LICENSE) file for details.

---

## Acknowledgments

See [ACKNOWLEDGMENTS.md](../.github/ACKNOWLEDGMENTS.md).

---

## Contact

See [SUPPORT.md](../.github/SUPPORT.md) for where to get help and expected response times.

---

## Glossary

| Term                         | Definition                                                                |
| ---------------------------- | ------------------------------------------------------------------------- |
| Configuration                | Settings defining providers, resources, and platform behavior             |
| Deployment                   | An instance of infrastructure/application in a specific environment       |
| Environment                  | A specific setup (dev, staging, prod) that overrides workspace defaults   |
| Firewall                     | Security rules governing network traffic to/from resources                |
| Infrastructure as Code (IaC) | Managing infrastructure through declarative code and automation           |
| Module                       | A deployable application component (source + lifecycle hooks)             |
| Namespace                    | A logical grouping of modules within a workspace                          |
| Platform                     | The core CLI and orchestration layer                                      |
| Profile                      | A named set of file references for a specific environment                 |
| Provider                     | A cloud service provider (Azure, AWS, GCP, Kamatera, local)               |
| Ref                          | A typed file reference (env, config, data, secret) attached to a profile  |
| Resource                     | An individual infrastructure component (VM, disk, network)                |
| Topology                     | The arrangement and relationships of resources within a workspace         |
| Workspace                    | A logical grouping of resources that defines WHAT infrastructure to build |

---

## Core Concepts

### Separation of Concerns

Each concern lives in its own repository:

| Repo type            | Example name         | Contains                                                   |
| -------------------- | -------------------- | ---------------------------------------------------------- |
| Platform (this repo) | `strata`       | CLI, provisioners, built-in defaults                       |
| Configuration        | `xyz-config`         | Provider credentials, topology definitions, firewall rules |
| Infrastructure       | `xyz-infrastructure` | Deployment manifests, Terraform backends                   |
| Service config       | `xyz-svc-<service>`  | Service-specific configuration files                       |

### Version Control Everything

- Each repository independently versioned and tagged
- Git commits form an immutable audit trail
- Tags mark approved configurations
- Branches support environment-specific variations

### Declarative Configuration

- All infrastructure defined in YAML — no imperative deployment scripts
- Reproducible deployments from manifests
- Configuration drift detectable via `xyz build plan`

### Audit Trail

Every `xyz build run` generates a deployment manifest capturing:
- Exact Git commits for all configuration sources
- Version tags for platform and modules
- Timestamp, user, and resource configuration
- Approval metadata (when configured)

This provides audit-ready evidence for regulatory frameworks (NIS2, ISAE 3402 Type 2).
