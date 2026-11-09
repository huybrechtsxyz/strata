# Deployment documentation (`strata deploy output`) - Work

- Status: proposed; nothing built yet
- Last updated: 2026-11-09
- Related: [deploy-command.md](../design/deploy-command.md) (the `deploy output` row in the v1 survey),
  [ADR-0036](../decisions/0036-cross-run-key-value-output-store.md) (`DeploymentOutputStore`),
  [values-secrets-command.md](../design/values-secrets-command.md) (`values list` and its `value_ref`),
  [store-integration-configuration.md](../design/store-integration-configuration.md) (how a secret store finds
  its vault), [audit-commands.md](../design/audit-commands.md) (the git audit sink as a durable destination)

## Overview

After a deploy, the people who use what was deployed need a handover document: the endpoints, IPs, image
versions and other step outputs, plus how to get the secrets that go with them (an admin password, a connection
string, an API key). Today strata stores all of this for programmatic use only. `DeploymentOutputStore` keeps step
outputs for later `${output:...}` references and the audit manifest records what was deployed, but nothing turns
either into something a person can read. [deploy-command.md](../design/deploy-command.md) already lists
`deploy output` as **Partial** for this reason.

This doc proposes a read command that renders that document, and an optional deployment setting that renders it
automatically at the end of `deploy run`.

The rule for secrets: **a secret's value never appears in the document, in any form, ever.** Not masked to its
last four characters, not as a length, not as "(empty)". A secret is always shown as the same fixed `********`,
whether it has a value, is empty, or does not resolve. In its place the document names **where the secret lives**
(store, vault, secret name, version), so the reader can find it and the operator can send it through a
one-time-link tool such as Password Pusher.

## Current state (before this work)

Verified against the source on 2026-11-09.

| Piece                                                       | What it gives a deployment document today                                                                                                                                                                                       |
| ----------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `collect_step_outputs()` (`deploy_controller.py`)           | `(values, sensitive_keys)` per step, in memory, for this invocation only.                                                                                                                                                       |
| `DeploymentOutputStore` (`deployment_output_store.py`)      | Non-sensitive outputs per step, with `collected_at`. Sensitive outputs are filtered out before writing, so **their names are not recorded either**. Only exists if the workspace sets `output_store`.                             |
| Audit manifest (`audit_manifest_model.py`)                  | What was deployed, by whom, which provisioner, policy results. Pushed to a remote by the git audit sink.                                                                                                                        |
| `strata deploy images`                                      | The effective image and tag per module.                                                                                                                                                                                         |
| `values list --declared-only` (`DeclaredValueRow`)          | Per secret: `key`, `store`, `value_ref` (the env var name, GitHub secret name or vault secret name), `generate`, `rotate`. The console view leaves out `value_ref`. For `store: constant`, `value_ref` **is the secret**.           |
| Store resolvers (`azure_keyvault_resolver.py`, `infisical_resolver.py`) | The vault location: Key Vault URL from the bound Integration's `spec.endpoints.address` or `AZURE_KEYVAULT_URL`; Infisical address, `project_id` and `environment` from the Integration's `configuration` or `INFISICAL_*` env vars. |
| `provisioner.output.template` / `validate_template_references()` | Jinja2 rendering with a check that every name the template uses exists.                                                                                                                                                    |

## Proposed design

### 1. `strata deploy output DEPLOYMENT`

A read-only command. It never calls a provisioner and never writes infrastructure.

```text
strata deploy output DEPLOYMENT [--format table|markdown] [--template PATH] [--output-path FILE]
                                [--step NAME]... [--path PATH] [--output console|json]
```

The flags follow [ADR-0056](../decisions/0056-output-is-the-result-format-is-the-content.md): `--output` for the
run, `--format` for the content, `--output-path` for a file:

- `--output console|json` (or `STRATA_OUTPUT`) is the **command's return value**, the same envelope and exit codes as
  every other command. With `--output json`, the envelope carries the structured data below (the template context).
  That is what a CI pipeline or another tool such as PSDocs consumes.
- `--format table|markdown` is the **document**: how a person reads it. `table` is console tables, the same style as
  `values list`. `markdown` is the built-in Markdown document, and `--template` replaces it with a Jinja2 template the
  user provides.
- `--output-path FILE` writes the document to a file, as `promote view` does. The two then work together: the
  document goes to the file and the envelope goes to stdout (with `--output json`, a small confirmation naming the
  file, not a second copy of the document). Without `--output-path`, the document goes to
  stdout under `--output console`. Under `--output json`, stdout must stay a single JSON document, so `--format` is
  ignored, exactly as in `values get`.

It gathers, without resolving any secret value:

1. **Step outputs** from `DeploymentOutputStore.read()`, for every execution step (or only `--step`), with each
   step's `collected_at`.
2. **Sensitive output names** for each step (see section 3); their values are always `********`.
3. **Secrets** the deployment references: the declared `SecretStoreModel` entries reachable from the deployment's
   environment, each with its location (section 2).
4. **Deployment context**: deployment name, environment, effective images (the `deploy images` rows), and the latest
   audit manifest entry if a git audit sink is configured (who deployed, when, from which commit).

Why a command rather than an execution step: a provisioner step changes infrastructure and has plan, apply and
destroy behaviour, which a document does not. A step would also only see the outputs from steps that ran in that
invocation, so a `--stage` or `--scope` run would give an incomplete document. The command reads the durable record,
so it can run again at any time, in its own CI job, without redeploying.

### 2. Secret locations instead of values

Every secret row carries a `location` that says where to find the secret, built from the declaration and the
resolver's connection settings. Connection settings only: no credentials, no tokens.

| `store`          | `location` fields                                                                       | Example                                                                       |
| ---------------- | --------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| `azure-keyvault` | `vault` (URL), `name` (`value`), `version` if pinned                                    | `https://kv-prd-weu.vault.azure.net/` · `sql-admin-password` · latest         |
| `infisical`      | `address`, `project_id`, `environment`, `path`, `name` (`value`), `version` if pinned   | `https://app.infisical.com` · `a1b2…` · `prod` · `/` · `SQL_ADMIN_PASSWORD`   |
| `github`         | `name` (the Actions secret), `repository` if it can be determined                       | `SQL_ADMIN_PASSWORD` in `org/repo`                                            |
| `environment`    | `name` (the env var)                                                                    | `SQL_ADMIN_PASSWORD` (set on the deploy runner)                               |
| `vault`, `bitwarden` | `name` (`value`) only, since there is no resolver to say which server              | `secret/data/prd/sql`                                                         |
| `constant`       | **no location.** `value_ref` is the secret, so it is never shown. The row says the secret is held in source, and which document declares it. | `declared in environments/prd.yaml` |

Rules:

- The `value` column is always `********`, for every store, including an empty or unresolvable secret. The command
  does not resolve secret values at all, so it cannot leak one by mistake.
- Locations come from the same code that resolves secrets: each `StoreIntegration` gets a small `location(key)`
  method that returns its non-secret connection settings. The command does not rebuild that logic itself, so it
  cannot drift from what `deploy run` actually reads.
- A `store: constant` secret also gets a warning in the document and on the console: a secret held in source cannot
  be handed over by location, and should probably move to a vault.
- If a location cannot be determined (for example `AZURE_KEYVAULT_URL` is not set where the command runs), the row
  says `unknown` and the command reports a warning, not an error. The document is still useful without it.

Example of the Markdown output for secrets:

```markdown
| Secret             | Value    | Store          | Where to find it                                               |
| ------------------ | -------- | -------------- | -------------------------------------------------------------- |
| SQL_ADMIN_PASSWORD | ******** | azure-keyvault | `kv-prd-weu.vault.azure.net` / `sql-admin-password` (latest)   |
| GRAFANA_API_KEY    | ******** | infisical      | project `a1b2…` / env `prod` / `/` / `GRAFANA_API_KEY`         |
| LEGACY_TOKEN       | ******** | constant       | held in source (`environments/prd.yaml`), move to a vault      |
```

### 3. Sensitive step outputs

A Terraform output marked `sensitive = true` is never written to `DeploymentOutputStore`. That stays. But today its
**name** is dropped too, so a document cannot even say it exists. Proposed change: the record also keeps
`sensitive_keys` (names only) per step, so the document can list `admin_password: ********`.

A sensitive output has no vault location: its value lives only in that step's Terraform state. The document says so
(`terraform state of step 'infra'`). If a team wants such a value handed over, the step should write it to a vault
(for example an `azurerm_key_vault_secret` resource) and the document then points at that secret like any other.
Whether strata should describe that link (output `admin_password` is stored as Key Vault secret `x`) is an open
question below.

### 4. Optional: render at the end of `deploy run`

A deployment-level block, off by default:

```yaml
spec:
  documentation:
    template: docs/handover.md.j2   # optional; built-in Markdown if omitted
    sink: audit                     # write next to the audit manifest and push with it
```

At the end of a successful `deploy run`, strata runs the same render as `deploy output --format markdown` and writes
the file into the audit sink, so each deploy leaves a dated handover document in the audit remote. This is a small
addition on top of section 1 and should be built after it.

### 5. Templates

- The template receives the same data as the `--output json` envelope: `deployment`, `steps[].outputs`, `steps[].sensitive_keys`,
  `secrets[]` (with `location`), `images[]`, `audit`.
- There is no variable in that context that holds a secret value, so a template cannot print one even by mistake.
- `strata validate` checks the template with `validate_template_references()`, so a typo fails before a deploy, not
  after.

### 6. PSDocs and PSDocs for Azure (PowerShell)

Teams on Azure may already use [PSDocs](https://github.com/microsoft/PSDocs), a PowerShell engine that generates
Markdown from objects using document definitions (`*.Doc.ps1`), and its module
[PSDocs for Azure](https://azure.github.io/PSDocs.Azure/overview), which generates Markdown from ARM templates
(parameters, outputs, resources, with descriptions from template metadata). They cover two different things:

| Tool               | Documents                                                       | When          | Fit with strata                                                                                     |
| ------------------ | --------------------------------------------------------------- | ------------- | --------------------------------------------------------------------------------------------------- |
| PSDocs             | Any object, rendered by a PowerShell document definition        | After deploy  | An alternative to the Jinja2 template: it renders the `deploy output --output json` envelope.       |
| PSDocs for Azure   | An ARM template: what it takes and produces, not deployed values | Before deploy | Documents the template a `bicep` provisioner deploys. Complements the handover document, does not replace it. |

**PSDocs as a renderer (no strata code needed).** The JSON envelope is the contract, so PSDocs works without
changes to strata:

```powershell
Import-Module PSDocs
strata deploy output prd-deployment --output json |
    ConvertFrom-Json |
    Invoke-PSDocument -Path ./docs/handover.Doc.ps1 -OutputPath ./out/docs
```

The secret rule still holds, because the envelope has no secret values for a PowerShell definition to print either.

**PSDocs for Azure for `bicep` provisioners.** PSDocs for Azure reads ARM JSON, so a Bicep source is compiled first
(`bicep build`), then passed to `Invoke-PSDocument -Module PSDocs.Azure -InputObject <template.json>`. Strata
could run this during `build run` for each `bicep` provisioner and link the resulting template document from the
handover document ("what this step deploys" next to "what it produced"). A `secureString` parameter appears by name
only, so this does not expose secrets.

**Recommendation.** Do not make PowerShell a dependency of strata itself. Strata is a cross-platform Python CLI, and
`pwsh` is not on every runner.

1. Document the PSDocs pipe above in the how-to (implementation plan step 6). This costs nothing and
   gives Azure teams their familiar tool.
2. Treat PSDocs for Azure in `build run` as an optional later step (open question 7). It only applies to `bicep` (and
   ARM) provisioners. Terraform, Helm and Compose would need their own equivalents (for example `terraform-docs` for
   Terraform modules).

## Remaining Work / Open Questions

1. **Linking a sensitive output to a vault secret.** Should a step be able to declare "output `admin_password` is
   also stored as Key Vault secret `x`", so the document points at the vault instead of Terraform state? Or is it
   enough to recommend writing the secret to a vault and declaring it as a normal secret?
2. **Who may see locations.** Vault URL and secret name are not secrets, but they do tell a reader where to look.
   Is that acceptable for every reader of the document, or does it need a `--no-locations` option (or a deployment
   setting) for documents that leave the team?
3. **Which secrets to list.** All secrets declared in the deployment's environment, or only those a step actually
   references (`graph.secret_refs`)? The second is more accurate, but needs the build graph.
4. **No `output_store` configured.** Without a durable record the command has no outputs to show. Options: report
   that and show secrets and images only, or fall back to running `terraform output -json` against the build path
   (which needs credentials and reachable state).
5. **Password Pusher integration.** Out of scope for now: strata only says where the secret is. A later
   `--push` option could read the secret and create a one-time link, but that would make this command read secret
   values, which this design deliberately avoids.
6. **GitHub repository name.** Whether the repository for a `github` secret can be found reliably (from the git
   remote or `GITHUB_REPOSITORY`), or should simply be left out.
7. **Template documentation per provisioner.** Should `build run` generate template-level documentation for each
   provisioner (PSDocs for Azure for `bicep`, `terraform-docs` for Terraform) as an optional, opt-in step that only
   runs when the tool is installed? Or should that stay outside strata, in the team's own pipeline? Needs a real team
   asking for it first.

## Implementation plan

1. `StoreIntegration.location(key)` on the Key Vault and Infisical resolvers, plus built-in locations for
   `environment`, `github` and `constant`. Unit tests check that no credential or token ends up in a location.
2. `DeploymentOutputStore` records `sensitive_keys` (names only) per step; reading an older record without them
   still works.
3. `strata deploy output` with `console` and `json` formats.
4. Built-in Markdown format and `--template`, with the template checked by `strata validate`.
5. `spec.documentation` on the deployment and writing the document to the audit sink.
6. Documentation: `docs/config/deployment.md` and a how-to for the Password Pusher handover workflow, including
   rendering the JSON envelope with PSDocs.

## Changelog

- 2026-11-09: created.
- 2026-11-09: added section 6 (PSDocs and PSDocs for Azure), open question 7 and the PSDocs how-to in step 6.
- 2026-11-09: split the flags: `--output console|json` is the command's return value, `--format table|markdown` is
  the document (the convention `values get` already follows). Renamed `--out` to `--output-path`, matching
  `promote view`. Both now follow ADR-0056.
