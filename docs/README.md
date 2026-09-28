# strata

strata is a **declarative YAML layer over Terraform, Helm, and Docker Compose**: infrastructure and
deployments are described once as plain YAML documents, validated as a whole, then rendered and
executed by whichever provisioner a workspace declares.

This is **v2** — a ground-up rebuild, redesigned from lessons learned tracing v1's real behaviour
(see [docs/decisions/](decisions/0001-v1-schema-analysis-findings-for-v2.md) for the full ADR trail).
For the current implementation status of each document kind, see
[docs/design/v2-schema-overview.md](design/v2-schema-overview.md) — this page is the practical,
task-focused guide; that one is the up-to-date status table.

## Table of Contents

- [Key Features](#key-features)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Document Shape](#document-shape)
- [CLI Reference](#cli-reference)
- [Deployment Workflow](#deployment-workflow)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [Security](#security)
- [License](#license)
- [Glossary](#glossary)

---

## Key Features

- **Declarative YAML configuration** — every document follows the same Kubernetes-style shape
  (`apiVersion`, `kind`, `meta`, `spec`), validated with Pydantic v2 in strict mode
  (`extra="forbid"` — unknown fields are a validation error, not a silent typo).
- **Documents, not file paths.** Every cross-document reference is `(kind, meta.name)` — never a
  path — so renaming or reorganizing files never breaks a reference.
- **Solution-wide discovery.** One `strata.yaml` (`kind: solution`) marks the root; every document
  under it is discovered and indexed automatically, in any directory layout you like.
- **Two-phase validation.** Schema checks (Pydantic) first, then cross-document semantics — dangling
  references, unresolved variables, mismatched kinds, stale version pins — all before anything is
  rendered or applied.
- **render, then execute.** `build run` renders a deployment's workspace into on-disk Terraform/
  Helm/Compose artifacts; `deploy run` is the only step that actually calls `terraform plan/apply`
  (or the Helm/Compose equivalent) — and only against what `build run` already wrote.
- **Pluggable value resolution.** `${var:KEY}` / `${secret:KEY}` / `${feature:KEY}` tokens resolve
  against named stores — constants, environment variables, or an integration-backed secret store —
  so the same document works unchanged across environments.
- **Scriptable by design.** `--output json` (or `STRATA_OUTPUT=json`) emits one structured JSON
  envelope per run on stdout, with stable exit codes — built for CI pipelines and AI agents, not
  just interactive use.

## Prerequisites

| Tool                                                   | Version | Required for                                       |
| ------------------------------------------------------ | ------- | -------------------------------------------------- |
| Python                                                 | 3.13+   | CLI runtime (dev target; `requires-python >=3.10`) |
| [uv](https://docs.astral.sh/uv/)                       | latest  | Package and environment management                 |
| [Terraform](https://developer.hashicorp.com/terraform) | 1.5+    | Only if a workspace uses a `terraform` provisioner |
| [Helm](https://helm.sh/)                               | 3.x     | Only if a workspace uses a `helm` provisioner      |
| [Docker Compose](https://docs.docker.com/compose/)     | v2      | Only if a workspace uses a `compose` provisioner   |

## Installation

Not published to PyPI yet — run from a development install:

```powershell
uv sync
.\.venv\Scripts\Activate.ps1
strata --help
```

Or without activating:

```powershell
uv run strata --help
```

## Quick Start

The [`config/`](../config/README.md) directory is a real, working solution — not a toy fixture —
so every command below actually runs against it:

```powershell
cd config

# Validate every document in the solution
strata validate

# Inspect resolved values before building anything
strata values get prd-deployment REGION PUBLIC_IP

# Render the deployment's workspace to disk (Terraform/Helm/Compose artifacts)
strata build run prd-deployment

# Report what deploy run would do, without calling any provisioner
strata deploy run prd-deployment --dry-run
```

## Document Shape

Every strata document follows the same shape:

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: deployment # see docs/design/v2-schema-overview.md for the full kind list
meta:
  name: prd-deployment
  annotations:
    description: "Production instance"
spec:
  ...
```

Full, current kind list and implementation status:
[docs/design/v2-schema-overview.md](design/v2-schema-overview.md).

## CLI Reference

| Command                               | Purpose                                                                       |
| ------------------------------------- | ----------------------------------------------------------------------------- |
| `strata validate [PATH]`              | Schema + cross-document validation for the whole solution                     |
| `strata values get DEPLOYMENT KEY...` | Resolve one or more variables/secrets/feature flags for a deployment          |
| `strata build run DEPLOYMENT`         | Render a deployment's workspace into on-disk artifacts                        |
| `strata deploy run DEPLOYMENT`        | Execute `build run`'s output (`plan`/`apply`, or the Helm/Compose equivalent) |
| `strata version`                      | Show the strata version                                                       |

Common options accepted by most commands:

| Option                   | Description                                                                   |
| ------------------------ | ----------------------------------------------------------------------------- |
| `--path PATH`            | Where to start looking for the solution (or `STRATA_WORK_PATH`; default: cwd) |
| `--output console\|json` | Output format (default: `console`; or `STRATA_OUTPUT`)                        |
| `--verbose` / `--quiet`  | More or less console output                                                   |

`build run`/`deploy run` also accept `--build-path` and `--dry-run`; `deploy run` adds `--stage`/
`--scope` to restrict which provisioning steps run. Run `strata <command> --help` for the full,
authoritative list.

## Deployment Workflow

1. `strata validate` — check every document's schema and cross-document references.
2. `strata values get` — inspect resolved variables/secrets/feature flags before building anything.
3. `strata build run` — render the deployment's workspace into on-disk Terraform/Helm/Compose
   artifacts. Renders only — never calls `plan`, `apply`, or `deploy`.
4. `strata deploy run` — execute what `build run` wrote: `terraform plan`/`apply` (or the Helm/
   Compose equivalent), per provisioning step, in dependency order.

## Testing

```powershell
& .\.venv\Scripts\python.exe -m mypy src
& .\.venv\Scripts\python.exe -m ruff check --fix src tests
& .\.venv\Scripts\lint-imports.exe
& .\.venv\Scripts\python.exe -m pytest -q
```

See [CONTRIBUTING.md](../.github/CONTRIBUTING.md) for the full workflow.

## Troubleshooting

| Symptom                                            | Likely cause                                          | Fix                                                          |
| -------------------------------------------------- | ----------------------------------------------------- | ------------------------------------------------------------ |
| `Not inside a strata solution: no strata.yaml ...` | Command run outside any solution tree                 | `cd` into the solution, or pass `--path`/`STRATA_WORK_PATH`  |
| Exit code `2`                                      | Bad arguments, or not inside a solution               | Check `strata <command> --help`                              |
| Exit code `3`                                      | Schema or cross-document validation failed            | Read the reported diagnostics — each names the failing field |
| Exit code `1`                                      | System failure (I/O, unexpected crash)                | Not a configuration problem — check the traceback/logs       |
| `... environment variable 'X' is not set`          | A `store: environment` secret/variable isn't exported | Export it before running `deploy run`/`build run --resolve`  |

Exit code reference: `0` success · `1` system failure · `2` usage error · `3` validation failure.

## Contributing

See [CONTRIBUTING.md](../.github/CONTRIBUTING.md) for workflow, conventions, and architecture rules.

## Security

See [SECURITY.md](../.github/SECURITY.md) for the vulnerability reporting policy.

## License

GNU Affero General Public License v3.0 (AGPL-3.0) — see [LICENSE](../LICENSE).

## Glossary

Full list of terms and concepts: [docs/GLOSSARY.md](GLOSSARY.md).

