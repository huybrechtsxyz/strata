# Workspace README

This `.strata` folder contains workspace state and helper files created by the Strata CLI.

Included files:

- `logging.yaml` — development logging configuration for local runs
- `cli.yaml` — CLI preference values for the workspace
- `.gitignore` — files to ignore from the `.strata` folder
- `integrations/` — per-integration help documents (one markdown file per integration)

## Using the integration docs

Each file under `.strata/integrations/` documents how to configure and use an integration (for example, `azure_keyvault.md`, `git.md`, `terraform.md`). Use these documents as the first-stop reference when wiring credentials, environment variables, or diagnosing common issues.

Examples:

- To see the Azure Key Vault guide: open `.strata/integrations/azure_keyvault.md`
- To find quick Git setup instructions: open `.strata/integrations/git.md`
- To find Bitwarden Secrets Manager setup: open `.strata/integrations/bitwarden.md`

## Available integrations

| File                   | Integration               |
| ---------------------- | ------------------------- |
| `azure_appconfig.md`   | Azure App Configuration   |
| `azure_keyvault.md`    | Azure Key Vault           |
| `bitwarden.md`         | Bitwarden Secrets Manager |
| `docker.md`            | Docker                    |
| `git.md`               | Git                       |
| `hashicorp_consul.md`  | HashiCorp Consul          |
| `hashicorp_vault.md`   | HashiCorp Vault           |
| `store_integration.md` | Generic Store             |
| `terraform.md`         | Terraform                 |

## Customising

You can safely edit these files in your workspace to add project-specific notes (for example, the vault name to use in your environment or a private registry URL). Local edits are intended to be committed into your repository.
