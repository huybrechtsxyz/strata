# Resolve secrets at build time, not deploy time

- Status: completed
- Date: 2025-07-16

## Context and Problem Statement

strata deployments reference secrets (API keys, passwords, tokens) that must be injected into Terraform variables at some point before `terraform apply` runs. The question is: when should secret values be fetched from the secret store (Bitwarden, Azure Key Vault, HashiCorp Vault, environment variables)?

Two natural points exist: during `strata build run` (build time) or during `strata deploy run` (deploy time). This choice affects security, cacheability, operator workflow, and offline usability.

## Considered Options

- **Build time** — `strata build run` fetches secrets and writes them into `terraform.tfvars.json` in the build output directory
- **Deploy time** — `strata deploy run` fetches secrets just before calling `terraform apply`, passing them as `-var` flags or writing ephemeral files
- **Hybrid** — fetch at build time, re-fetch at deploy time if the build is stale

## Decision Outcome

Chosen: **Build time**, because it separates the concerns of "prepare everything needed for deployment" (`build`) from "execute the deployment" (`deploy`), and allows operators to inspect and validate the full build output — including resolved variable values — before any infrastructure change is made.

### Consequences

- Good: `strata build plan` can show complete, accurate Terraform plans because all variable values are present in the build output.
- Good: Operators can review `terraform.tfvars.json` before deploying — no hidden values injected at apply time.
- Good: `strata deploy run` becomes a pure orchestration step — it calls `terraform apply` against an already-complete artifact directory, with no secret store dependency at deploy time.
- Good: CI pipelines can separate the `build` stage (needs secret store access) from the `deploy` stage (needs only the build output artifact and Terraform state access).
- Bad: The build output directory contains secret values in plaintext. It must be treated as a sensitive artifact — not committed to version control, not uploaded as a CI artifact without encryption.
- Bad: If secrets rotate between `build run` and `deploy run`, the build output is stale. Operators must rebuild before deploying after a rotation.
- Bad: Offline builds (no secret store access) are not possible when secrets are required.

## Security Considerations

The build output path (`.strata/build/`) is excluded from version control via `.gitignore`. CI pipelines that upload build artifacts should treat them as sensitive (encrypted artifact storage, short TTL). strata never writes secret values to `docs/`, committed config YAML files, or any path outside `.strata/build/`.

The secret store itself is never directly accessed by Terraform — strata fetches the value and injects it as a Terraform variable. This means Terraform's state contains the resolved value (standard Terraform behaviour), but the secret store credentials are never embedded in Terraform configuration.

## Pros and Cons of the Options

### Deploy time

- Good: Secrets are as fresh as possible — rotated credentials are picked up automatically.
- Good: Build output contains no sensitive values — safe to inspect, share, or cache.
- Bad: `strata build plan` cannot produce accurate plans without secret store access — defeats the "review before deploying" workflow.
- Bad: `deploy run` has a dependency on the secret store being available and authenticated — network partition or vault downtime blocks deployment even if the Terraform itself is ready.

### Hybrid

- Good: Fresh secrets at deploy time while keeping the build-time inspection benefits.
- Bad: Adds complexity — two fetch paths, potential for inconsistency if build-time and deploy-time values differ.
- Bad: Still requires secret store access at deploy time, reintroducing the availability dependency.

## More Information

Secret references in YAML use `store: bitwarden | azure_keyvault | hashicorp_vault | env` with a `value` field pointing to the secret identifier (Bitwarden item UUID, Key Vault secret name, Vault path, or env var name). The actual secret value is fetched during `build run` and written into `terraform.tfvars.json`.

Related: [Value proposition — escape hatch](../platform/value-proposition.md), [Environment configuration](../config/environment.md), [Integrations reference](../platform/integrations.md)
