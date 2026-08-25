# strata GitHub Actions Catalog

Composite GitHub Actions for setting up strata itself and every integration it
supports. All `setup-<integration>` actions follow the same convention:

- **CLI-based integrations** (the strata integration shells out to a real binary,
  e.g. `vault`, `consul`, `terraform`) → the action **installs the CLI**.
- **API-only integrations** (strata talks to a REST API directly, e.g. `flagsmith`,
  SIEM sinks, identity providers) → the action **sets the required env vars**.
- **Dual-mode integrations** (a CLI exists but isn't strictly required, e.g.
  `infisical` which falls back to its REST API; or an integration supports more
  than one auth method, e.g. `vault`'s token/approle/kubernetes) → the action
  exposes an `auth-method` / `install-cli` / `backend` **input parameter to choose**.

## Security conventions (all actions)

- Secrets are always passed via `env:` in the composite step, never interpolated
  directly into `run:` — this prevents command injection from a secret value
  containing `$(...)`.
- Secrets are masked with `echo "::add-mask::$VALUE"` before being written anywhere.
- Values are written to `$GITHUB_ENV` using the `NAME<<__STRATA_DELIM__ ... __STRATA_DELIM__`
  heredoc form, which is immune to newline injection (a naive `echo "VAR=$VALUE"
  >> $GITHUB_ENV` lets an embedded newline in the secret define extra env vars).

## Prerequisite

`setup-strata` must run before any command that invokes the `strata` CLI. It does
not need to run before a `setup-<integration>` action — those only prepare the
CLI/env vars an integration needs; order relative to `setup-strata` doesn't matter.

## Catalog

| Action                      | Integration type(s)                       |        Installs CLI        |       Sets env vars       | Notes                                                                                     |
| --------------------------- | ----------------------------------------- | :------------------------: | :-----------------------: | ----------------------------------------------------------------------------------------- |
| `setup-strata`              | — (the strata CLI itself)                 |             ✅              |             —             | Required before any strata command                                                        |
| `setup-bitwarden`           | `bitwarden`                               |             ✅              |             ✅             | `bws` CLI required                                                                        |
| `setup-yq-cli`              | — (helper tool, not a strata integration) |             ✅              |             —             |                                                                                           |
| `verify-integrations`       | multiple store types (legacy, generic)    |             —              |             ✅             | Superseded by the dedicated actions below for new workflows                               |
| `setup-infisical`           | `infisical`                               |  optional (`install-cli`)  |             ✅             | REST API works without the CLI                                                            |
| `setup-vault`               | `hashicorp_vault`                         |        ✅ (required)        |             ✅             | `auth-method`: token / approle / kubernetes                                               |
| `setup-consul`              | `hashicorp_consul`                        |        ✅ (required)        |             ✅             |                                                                                           |
| `setup-openbao`             | `openbao`                                 |        ✅ (required)        |             ✅             | Reuses `VAULT_*` env var names                                                            |
| `setup-etcd`                | `etcd`                                    |        ✅ (required)        |             ✅             |                                                                                           |
| `setup-azure-cli`           | `azure_cli`                               |             ✅              |             ✅             | `auth-method`: client_secret / oidc                                                       |
| `setup-azure-keyvault`      | `azure_keyvault`                          |        ✅ (via `az`)        |             ✅             |                                                                                           |
| `setup-azure-appconfig`     | `azure_appconfig`                         |        ✅ (via `az`)        |             ✅             |                                                                                           |
| `setup-aws-cli`             | `aws_cli`                                 |             ✅              |             ✅             | `auth-method`: keys / oidc                                                                |
| `setup-gcloud-cli`          | `gcloud_cli`                              |             ✅              |             ✅             | `auth-method`: service_account_key / workload_identity                                    |
| `setup-terraform-cli`       | `terraform`                               |             ✅              |   optional (TFC token)    | Wraps `hashicorp/setup-terraform`                                                         |
| `setup-opentofu`            | `opentofu`                                |             ✅              | optional (backend token)  | Wraps `opentofu/setup-opentofu`                                                           |
| `setup-helm`                | `helm`                                    |             ✅              |             —             | Wraps `azure/setup-helm`                                                                  |
| `setup-ansible`             | `ansible`                                 |             ✅              |             —             | `pip install ansible-core`                                                                |
| `setup-checkov`             | `checkov`                                 |             ✅              |             —             | `pip install checkov`                                                                     |
| `setup-opa`                 | `opa`                                     |             ✅              |             —             |                                                                                           |
| `setup-infracost`           | `infracost`                               |             ✅              |             ✅             |                                                                                           |
| `setup-cve-scanner`         | `cve_scanner`                             |             ✅              |             —             | `backend`: trivy / grype                                                                  |
| `setup-docker`              | `docker`                                  | verify only (preinstalled) | optional (registry login) |                                                                                           |
| `setup-git`                 | `git`                                     | verify only (preinstalled) |             —             | Optional commit identity                                                                  |
| `setup-flagsmith`           | `flagsmith`                               |             —              |             ✅             | API-only                                                                                  |
| `setup-ai-agent`            | `ai_agent`                                |             —              |         optional          | `provider`: ollama/openai/azure_openai/anthropic/azure_cli — ollama/azure_cli need no key |
| `setup-generic-oidc`        | `generic_oidc`                            |             —              |             ✅             | API-only, configurable env var names                                                      |
| `setup-azure-ad-identity`   | `azure_ad`                                |             —              |             ✅             | API-only                                                                                  |
| `setup-google-identity`     | `google` (identity)                       |             —              |             ✅             | API-only                                                                                  |
| `setup-aws-identity-center` | `aws_identity_center`                     |             —              |             ✅             | API-only                                                                                  |
| `setup-auth0`               | `auth0`                                   |             —              |             ✅             | API-only                                                                                  |
| `setup-github-oauth`        | `github_oauth`                            |             —              |             ✅             | API-only                                                                                  |
| `setup-sentinel`            | `sentinel`                                |             —              |             ✅             | HTTP sink                                                                                 |
| `setup-elk`                 | `elk`                                     |             —              |             ✅             | HTTP sink                                                                                 |
| `setup-otel`                | `otel`                                    |             —              |             ✅             | HTTP sink                                                                                 |
| `setup-splunk`              | `splunk`                                  |             —              |             ✅             | HTTP sink                                                                                 |
| `setup-webhook-sink`        | `webhook`                                 |             —              |             ✅             | HTTP sink                                                                                 |
| `setup-syslog`              | `syslog`                                  |             —              |             ✅             | Connection settings, token optional                                                       |

## Usage pattern

```yaml
- uses: huybrechtsxyz/strata/.github/actions/setup-strata@v1

- uses: huybrechtsxyz/strata/.github/actions/setup-infisical@v1
  with:
    client-id: ${{ secrets.INFISICAL_CLIENT_ID }}
    client-secret: ${{ secrets.INFISICAL_CLIENT_SECRET }}
    project-id: 50911d47-58d9-4fab-8b47-ffe385614011

- run: strata values get MY_SECRET -f config/deployment.yaml --output json
```

Each action's own `action.yml` header comment documents its exact inputs and the
strata integration it targets. `README.md` files are intentionally not duplicated
per-action here — this catalog is the single source of truth for the full list;
`setup-strata`, `setup-bitwarden`, and `setup-yq-cli` retain their original
per-action `README.md` from before this catalog existed.
