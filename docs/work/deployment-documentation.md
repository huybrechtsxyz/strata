# Deployment output and report (`strata deploy output`, `strata deploy report`) - Work

- Status: proposed; nothing built yet
- Last updated: 2026-11-09
- Related: [deploy-command.md](../design/deploy-command.md) (the `deploy output` and `deploy show` rows in the v1
  survey),
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

This doc proposes two read commands over the same durable record, and an optional deployment setting:

- **`strata deploy output`** is what a DevOps engineer expects from the name: the equivalent of `terraform output`,
  across every step of a deployment and without Terraform state. It lists outputs, or prints one bare value for a
  script. It closes the `deploy output` gap in [deploy-command.md](../design/deploy-command.md).
- **`strata deploy report`** renders the handover document: outputs plus secret locations, images and who deployed
  what. Named `report`, not `show`: [deploy-command.md](../design/deploy-command.md) keeps `deploy show` for v1's
  "print the resolved deployment config" (a pre-deploy view), and `terraform show` means full state or plan detail.
- **`spec.documentation`** on a deployment renders the report automatically at the end of `deploy run`.

The rule for secrets: **a secret's value never appears in the document, in any form, ever.** Not masked to its
last four characters, not as a length, not as "(empty)". A secret is always shown as the same fixed `********`,
whether it has a value, is empty, or does not resolve. In its place the document names **where the secret lives**
(store, vault, secret name), so the reader can find it and the operator can send it through a one-time-link tool
such as Password Pusher.

What "secret" covers, exactly: every value declared under `secrets:` in an environment, and every step output marked
`sensitive`. A Terraform output that is not marked `sensitive` is shown as recorded, so a secret that Terraform itself
does not know is sensitive would be printed. Terraform makes this rare: an output derived from a sensitive input
(including `var.strata_secrets`, declared `sensitive = true`) must itself be marked `sensitive`, or `terraform plan`
fails.

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

### 1. Two commands over one record

Both commands are read-only. They never call a provisioner, never write infrastructure, and never read a secret
value. Both read the per-step durable record that `deploy run` writes (what it holds, and the prerequisite, are
below). Both follow [ADR-0056](../decisions/0056-output-is-the-result-format-is-the-content.md): `--output` for the
run, `--format` for the content, `--output-path` for a file.

#### `strata deploy output`: the `terraform output` equivalent

```text
strata deploy output DEPLOYMENT [STEP | STEP.KEY] [--format table|raw] [--path PATH] [--output console|json]
```

| Command                                                       | Like                         | Prints                                                       |
| ------------------------------------------------------------- | ---------------------------- | ------------------------------------------------------------ |
| `strata deploy output web-prd`                                | `terraform output`           | Every step's outputs, one table per step                      |
| `strata deploy output web-prd infra`                          | `terraform output` in `infra` | That step's outputs                                          |
| `strata deploy output web-prd infra.sql_server_fqdn`          | `terraform output NAME`      | One output                                                   |
| `strata deploy output web-prd infra.sql_server_fqdn --format raw` | `terraform output -raw NAME` | The bare value and nothing else, for `$(...)` in a script |
| `strata deploy output web-prd --output json`                  | `terraform output -json`     | Every output in the standard envelope's `data`               |

- `STEP.KEY` is the same address as an `${output:STEP.KEY}` token, so what a person looks up is what a document
  references.
- Terraform's `-raw` becomes `--format raw`, as in `values get`: ADR-0056 keeps content shapes on `--format`.
  `raw` needs a single `STEP.KEY`, otherwise it is a usage error. Under `--output json`, `--format` does not apply.
- A sensitive output shows as `<sensitive>`, as `terraform output` does. One deliberate difference: `terraform
  output -raw NAME` prints a sensitive value, but strata never stores it, so `--format raw` on a sensitive output fails
  with an error that names the output and shows its description, which may say where else the value is kept
  (section 3).
- Each step's `collected_at` is shown in the table. Descriptions are not: the table stays as short as Terraform's.
  The JSON data carries them.
- No secret locations, images or audit information. That is `deploy report`.

#### `strata deploy report`: the handover document

```text
strata deploy report DEPLOYMENT [--format table|markdown] [--template PATH] [--output-path FILE]
                                [--step NAME]... [--path PATH] [--output console|json]
```

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

#### What the record holds

`deploy output` uses items 1 and 2. `deploy report` uses all four, without resolving any secret value:

1. **Step outputs** from the durable record, for every execution step (or only `--step`), with each step's
   `collected_at` and each output's `description` (section 3). Only Terraform and OpenTofu steps (and GitOps steps)
   produce outputs: `collect_step_outputs()` calls the tool's `terraform output -json`, Helm has no outputs, and
   Compose steps never reach that call. A Helm or Compose step is listed as "no outputs (the tool has none)".
2. **Sensitive output names** and descriptions for each step (section 3); their values are always `********`.
3. **Secrets each step used**, each with its location and description, also from the durable record (section 2).
   Only secrets a step actually used: a secret declared in a reachable environment but never used is left out. With
   `--step`, only the secrets those steps used are listed.
4. **Deployment context**: deployment name, environment, effective images (the `deploy images` rows), and the latest
   audit manifest entry if a git audit sink is configured (`deployed_by`, deploy time, `commit_sha`).

**Which secrets a step used** is recorded at `deploy run`, per step, not worked out from the YAML afterwards. A YAML
walk would have to know exactly which documents each tool reads, and a document it missed would drop a secret from
the document without any warning. At deploy time strata already scans each step's real input for `${output:}`
tokens (`referenced_output_keys()`, [output-declarations.md](../design/output-declarations.md)): the Terraform step
payload and backend configuration, each Helm module's rendered values, the merged Compose document. A sibling
`referenced_secret_keys()` scans the same input for `${secret:KEY}` tokens, plus script provisioner `args`. Secrets
reach every tool only through these tokens, so this is the exact set.

One exception: a provisioner's `output.template` receives every declared secret (`capabilities.py`), not only
tokens. The names a template uses come from `_referenced_keys()` (`utils/templater.py`), the helper behind
`validate_template_references()`. That helper skips dynamic access such as `secrets[name]`, so a template that
accesses `secrets` dynamically counts as using every declared secret.

Not `graph.secret_refs`: despite its name, `build_value_references()` fills it with every secret declared in the
reachable environments, used or not.

**Prerequisite: the workspace sets `output_store`.** The durable record is the only source of step outputs and
secret locations. Without it, the command stops with an error that names the missing `output_store` setting, and
renders nothing. Reading the record still needs network access and credentials for the store's own backend, like
any other `output_store` read. What the command never needs is provider credentials or Terraform state: it does not
fall back to `terraform output -json` against the build path. A step the store has no record for yet (the store was
configured after its last deploy, or the step has never run) is listed as "no outputs recorded", which is not an
error.

**Old records are shown, not refused.** `DeploymentOutputStore.read()` raises `OutputRecordStaleError` when a
record is older than the workspace's `output_store_max_age`, because a deploy must not consume a stale value. A
person reading values is different: `deploy output` and `deploy report` read without that limit and show each
step's `collected_at`, with a warning for a step older than `output_store_max_age`.

Why a command rather than an execution step: a provisioner step changes infrastructure and has plan, apply and
destroy behaviour, which a document does not. A step would also only see the outputs from steps that ran in that
invocation, so a `--stage` or `--scope` run would give an incomplete document. The command reads the durable record,
so it can run again at any time, in its own CI job, without redeploying.

### 2. Secret locations instead of values

Every secret row carries a `location` that says where to find the secret, built from the declaration and the
resolver's connection settings. Connection settings only: no credentials, no tokens.

The location is **recorded at `deploy run`**, in the same per-step record as the outputs, not worked out when the
document is rendered. Several connection settings come from environment variables (`AZURE_KEYVAULT_URL`,
`INFISICAL_ADDR`, `INFISICAL_PROJECT_ID`, `INFISICAL_ENVIRONMENT`) that are often set only on the CI runner. Worked
out on a laptop, the location would be `unknown`, or wrong if those variables point somewhere else. Recorded at
deploy time, it is where the deploy actually read the secret.

| `store`          | `location` fields                                                                       | Example                                                                       |
| ---------------- | --------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- |
| `azure-keyvault` | `vault` (URL), `name` (`value`); always the latest version                              | `https://kv-prd-weu.vault.azure.net/` · `sql-admin-password` · latest         |
| `infisical`      | `address`, `project_id`, `environment`, `path`, `name` (`value`)                        | `https://app.infisical.com` · `a1b2…` · `prod` · `/` · `SQL_ADMIN_PASSWORD`   |
| `github`         | `name` (the env var the workflow maps the secret into); no repository                  | GitHub Actions secret, read from env var `SQL_ADMIN_PASSWORD`; see the workflow for its source |
| `environment`    | `name` (the env var)                                                                    | `SQL_ADMIN_PASSWORD` (set on the deploy runner)                               |
| `vault`, `bitwarden` | `name` (`value`) only, since there is no resolver to say which server              | `secret/data/prd/sql`                                                         |
| `constant`       | **no location.** `value_ref` is the secret, so it is never shown. The row says the secret is held in source, and which document declares it. | `declared in environments/prd.yaml` |

Rules:

- The `value` column is always `********`, for every store, including an empty or unresolvable secret. Neither
  the two commands nor the recording step reads a secret value to build a location, so neither can leak one.
- Locations come from the same code that resolves secrets: each `StoreIntegration` gets a small `location(key)`
  method that returns its non-secret connection settings, called at `deploy run` with the same configuration the
  resolver just used. Strata does not rebuild that logic elsewhere, so the location cannot drift from what the deploy
  actually read.
- **No version is shown**, because none is used: the resolvers always read the latest version. `SecretStoreModel`
  accepts `version`, but `resolve_values()` calls `integration.resolve(str(store.value))` and never passes it on.
  Showing a pinned version would claim something the deploy did not do. Decided: `strata validate` warns on any
  secret that sets `version`, saying it has no effect yet and the latest version is read. If a resolver later honours
  `version`, the location shows it and that resolver's warning goes away.
- **Which document declares a secret** (`declared_in`, used for `constant`): `merge_environment_models()` keeps only
  the winning declaration, not where it came from. The recording step works it out from the same merge order: the
  last reachable environment that declares the key wins, so that is the one named.
- A `store: constant` secret also gets a warning in the document and on the console: a secret held in source cannot
  be handed over by location, and should probably move to a vault.
- If a location cannot be determined (for example `AZURE_KEYVAULT_URL` is not set where the command runs), the row
  says `unknown` and the command reports a warning, not an error. The document is still useful without it.
- **A Key Vault `value` is only ever a secret name.** The store and its integration say which vault, so the
  location is always the integration's vault plus the name. Decided: `strata validate` rejects a URL as the `value`
  of an `azure-keyvault` secret. The resolver passes `value` straight to `SecretClient.get_secret(name)`. Tested with
  a capturing transport, no network: a full URL gets URL-encoded into the name
  (`GET …/secrets/https%3A%2F%2Fkv-example.vault.azure.net%2Fsecrets%2Fappgw-cert/`), which can never match a
  Key Vault name (letters, digits and dashes only). The error suggests a `variable` when the address itself is what
  is wanted, which was the case in `config/`: App Gateway's `key_vault_secret_id` needs the secret's address, not its
  content. `APPGW_CERT_SECRET_ID` is now a `store: constant` variable used as `${var:...}`.
- A `github` secret is described as "GitHub Actions secret, read from env var `NAME`; see the workflow for its
  source", and nothing more. Strata only reads the environment variable; the workflow maps the GitHub secret into it
  (`env: NAME: ${{ secrets.OTHER_NAME }}`). Strata cannot know which repository, GitHub environment or organization
  holds the secret, nor whether the secret itself has the same name. `GITHUB_REPOSITORY` exists only inside GitHub
  Actions, and the solution's git remote is not necessarily where the secret is defined. So the document does not
  guess.

Example of the Markdown output for secrets:

```markdown
| Secret             | Value    | Store          | Where to find it                                               | Description            |
| ------------------ | -------- | -------------- | -------------------------------------------------------------- | ---------------------- |
| SQL_ADMIN_PASSWORD | ******** | azure-keyvault | `kv-prd-weu.vault.azure.net` / `sql-admin-password` (latest)   | SQL server admin login |
| GRAFANA_API_KEY    | ******** | infisical      | project `a1b2…` / env `prod` / `/` / `GRAFANA_API_KEY`         |                        |
| LEGACY_TOKEN       | ******** | constant       | held in source (`environments/prd.yaml`), move to a vault      |                        |
```

### 3. Sensitive step outputs

A Terraform output marked `sensitive = true` is never written to `DeploymentOutputStore`. That stays. But today its
**name** is dropped too, so a document cannot even say it exists. Proposed change: the record also keeps
`sensitive_keys` (names only) per step, so the document can list `admin_password: ********`.

A sensitive output is listed under its own output name, with `********` as its value. Strata does not know where
else the value lives, so it does not say. Instead the document shows the output's own Terraform `description`, which
leaves the choice to whoever writes the Terraform. If the step also writes the value to a vault (for example an
`azurerm_key_vault_secret` resource), the description can say so:

```hcl
output "sql_connection_string" {
  value       = "Server=tcp:..."
  sensitive   = true
  description = "Also stored in Key Vault kv-web-prd as secret sql-connection-string"
}
```

An output without a description shows only its name and `********`. Nothing in strata's schema links an output to a
vault secret; the description is free text, for people.

Descriptions apply to every output, not just sensitive ones, and are recorded at `deploy run` time next to the
values. `terraform output -json` does not return them (only `value`, `type` and `sensitive`), so strata reads them
from the step's root module `.tf` files with `python-hcl2`, already a dependency for the SBOM Terraform collector.
A description is source code, not a secret, so it is stored even for a sensitive output. A later `deploy output` or
`deploy report` in another CI job then needs neither the build directory nor Terraform.

Secret declarations work the same way: a `SecretStoreModel`'s own `description` is shown in the secrets table, so
whoever writes the environment decides what a reader sees next to the location.

### 4. Optional: render at the end of `deploy run`

A deployment-level block, off by default:

```yaml
spec:
  documentation:
    template: docs/handover.md.j2   # optional; built-in Markdown if omitted
    sink: audit                     # write next to the audit manifest and push with it
```

At the end of a successful `deploy run`, strata runs the same render as `deploy report --format markdown` and writes
the file into the audit sink, so each deploy leaves a dated handover document in the audit remote. This is a small
addition on top of section 1 and should be built after it.

- `strata validate` rejects `spec.documentation` when the workspace has no `output_store`, or when `sink: audit` is
  set and no git audit sink is configured. Both gaps show up before a deploy, not at the end of one.
- Rendering runs after every step has applied. If it fails (a template error at render time, the audit push
  failing), `deploy run` reports a warning and still exits 0: the infrastructure is deployed, and failing the run
  would invite a redeploy that changes nothing. `strata deploy report` can render the document again afterwards.

### 5. Templates

- The template receives the same data as the `--output json` envelope: `deployment`, `steps[].outputs`, `steps[].sensitive_keys`,
  `secrets[]` (with `location`), `images[]`, `audit`.
- There is no variable in that context that holds a secret value, so a template cannot print one even by mistake.
- **The document is private.** It is meant for the team that runs the deployment, like the audit record it is
  stored next to, and is treated with the same care. Secret locations (vault URL, secret name), IPs and hostnames
  are not secrets, but they do tell a reader where to look. The built-in Markdown includes them. Whoever writes a
  template decides what goes into a document for a wider audience, for example by leaving out `secrets[].location`.
  Strata has no `--no-locations` option: the one guarantee it gives is that no secret value can be printed, and
  everything else is the template author's choice.
- `strata validate` checks the template with `validate_template_references()`, so a typo fails before a deploy, not
  after.

### 6. PSDocs and PSDocs for Azure (PowerShell)

Teams on Azure may already use [PSDocs](https://github.com/microsoft/PSDocs), a PowerShell engine that generates
Markdown from objects using document definitions (`*.Doc.ps1`), and its module
[PSDocs for Azure](https://azure.github.io/PSDocs.Azure/overview), which generates Markdown from ARM templates
(parameters, outputs, resources, with descriptions from template metadata). They cover two different things:

| Tool               | Documents                                                       | When          | Fit with strata                                                                                     |
| ------------------ | --------------------------------------------------------------- | ------------- | --------------------------------------------------------------------------------------------------- |
| PSDocs             | Any object, rendered by a PowerShell document definition        | After deploy  | An alternative to the Jinja2 template: it renders the `deploy report --output json` envelope.       |
| PSDocs for Azure   | An ARM template: what it takes and produces, not deployed values | Before deploy | Documents the template a `bicep` provisioner deploys. Complements the handover document, does not replace it. |

**PSDocs as a renderer (no strata code needed).** The JSON envelope is the contract, so PSDocs works without
changes to strata:

```powershell
Import-Module PSDocs
strata deploy report prd-deployment --output json |
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
2. Treat PSDocs for Azure in `build run` as an optional later step (Deferred item 1). It only applies to `bicep` (and
   ARM) provisioners. Terraform, Helm and Compose would need their own equivalents (for example `terraform-docs` for
   Terraform modules).

### 7. Example: from deploy to handover

A deployment `web-prd` with two execution steps: `infra` (Terraform: network, App Gateway, SQL server) and `apps`
(Helm: the web app). The workspace sets `output_store`, so `deploy run` recorded both steps: the outputs of `infra`,
and for each step the secrets it used. All names, addresses and IDs below are made up.

**What the solution declares.** The environment's secrets, in three different stores:

```yaml
# environments/web-prd.yaml
spec:
  secrets:
    - key: SQL_ADMIN_PASSWORD
      store: azure-keyvault
      value: sql-admin-password            # bare name: the vault comes from the azure-keyvault integration
      description: "SQL server admin login"
    - key: APPGW_CERT_NAME
      store: azure-keyvault
      value: appgw-cert
    - key: DB_PASSWORD
      store: environment
      value: WEB_DB_PASSWORD               # set on the deploy runner, from a CI secret
    - key: LEGACY_API_TOKEN
      store: constant
      value: "not-shown-anywhere"          # the value is the secret, so the document shows no location
```

`infra` uses the first three through `${secret:...}` tokens in its Terraform payload, and `apps` uses
`LEGACY_API_TOKEN` in a Helm module's values. A fifth secret declared here but used by no step would not appear in the
document.

The `infra` Terraform root has an output marked `sensitive = true`, whose description says where else the value is
kept:

```hcl
output "sql_connection_string" {
  value       = "Server=tcp:${azurerm_mssql_server.this.fully_qualified_domain_name};..."
  sensitive   = true
  description = "Also stored in Key Vault kv-web-prd as secret sql-connection-string"
}

output "sql_server_fqdn" {
  value       = azurerm_mssql_server.this.fully_qualified_domain_name
  description = "SQL server host name, for connection strings"
}
```

**Reading outputs, as with `terraform output`:**

```text
$ strata deploy output web-prd
infra (terraform) — collected 2026-11-09 14:28 UTC
  app_gateway_public_ip  20.61.14.7
  sql_connection_string  <sensitive>
  sql_server_fqdn        sql-web-prd.database.windows.net
  storage_account_name   stwebprd01
apps (helm) — no outputs (Helm has none)

$ strata deploy output web-prd infra.sql_server_fqdn --format raw
sql-web-prd.database.windows.net

$ strata deploy output web-prd infra.sql_connection_string --format raw
error: 'infra.sql_connection_string' is sensitive; strata never stores its value.
       Description: Also stored in Key Vault kv-web-prd as secret sql-connection-string
```

In a later CI job: `$fqdn = strata deploy output web-prd infra.sql_server_fqdn --format raw`.

**Rendering the document in CI**, the job after `deploy run`:

```powershell
$env:STRATA_OUTPUT = "json"
strata deploy report web-prd --format markdown --output-path out/web-prd-handover.md
```

Because `--output-path` is set, the document goes to the file and stdout carries only the envelope, whose `data`
confirms what was written:

```json
{
  "ok": true,
  "command": "deploy report",
  "summary": { "errors": 0, "warnings": 1, "info": 0 },
  "diagnostics": [
    { "severity": "warning", "code": "secret_in_source",
      "message": "Secret 'LEGACY_API_TOKEN' is held in source (environments/web-prd.yaml); it cannot be handed over by location. Move it to a vault." }
  ],
  "data": { "deployment": "web-prd", "written_to": "out/web-prd-handover.md", "format": "markdown" }
}
```

**The generated document**, `out/web-prd-handover.md`:

````markdown
# web-prd — deployment handover

Deployed 2026-11-09 14:32 UTC by `ci-pipeline` from commit `4602813` (audit record `web-prd/2026-11-09T14-32-05Z`).
Environment: `web-prd`. Outputs as recorded by that run; this is a snapshot, not a live view.

## Step outputs

### infra (terraform) — collected 2026-11-09 14:28 UTC

| Output                | Value                            | Description                                                          |
| --------------------- | -------------------------------- | -------------------------------------------------------------------- |
| app_gateway_public_ip | 20.61.14.7                       |                                                                      |
| sql_server_fqdn       | sql-web-prd.database.windows.net | SQL server host name, for connection strings                         |
| storage_account_name  | stwebprd01                       |                                                                      |
| sql_connection_string | ********                         | Also stored in Key Vault kv-web-prd as secret sql-connection-string  |

### apps (helm) — deployed 2026-11-09 14:31 UTC

No outputs (Helm has none).

## Images

| Module | Image                                   | Tag    | Source |
| ------ | --------------------------------------- | ------ | ------ |
| web    | acrwebprd.azurecr.io/web/frontend       | 3.14.2 | pin    |
| api    | acrwebprd.azurecr.io/web/api            | 3.14.2 | pin    |

## Secrets

Values are never included. Ask the operator for a one-time link, or fetch the secret from the location below if you
have access.

| Secret               | Value    | Store          | Where to find it                                                  | Description            |
| -------------------- | -------- | -------------- | ----------------------------------------------------------------- | ---------------------- |
| SQL_ADMIN_PASSWORD   | ******** | azure-keyvault | vault `kv-web-prd.vault.azure.net`, secret `sql-admin-password` (latest) | SQL server admin login |
| APPGW_CERT_NAME      | ******** | azure-keyvault | vault `kv-web-prd.vault.azure.net`, secret `appgw-cert` (latest)  |                        |
| DB_PASSWORD          | ******** | environment    | env var `WEB_DB_PASSWORD` on the deploy runner                    |                        |
| LEGACY_API_TOKEN     | ******** | constant       | held in source (`environments/web-prd.yaml`); move to a vault     |                        |
````

**Handing a secret over.** The reader asks for the SQL admin password. The operator, who has read access to the
vault, uses the location from the document:

```powershell
az keyvault secret show --vault-name kv-web-prd --name sql-admin-password --query value -o tsv
```

and pastes the value into Password Pusher with a short expiry and a single view, then sends the link. Strata never
reads the value, and the document never contains it. For `sql_connection_string`, the operator follows the output's
description to secret `sql-connection-string` in the same vault.

**The same data for PSDocs.** Without `--output-path`, `--output json` puts the full data in the envelope instead:

```json
"data": {
  "deployment": { "name": "web-prd", "environment": "web-prd" },
  "audit": { "deployed_at": "2026-11-09T14:32:05Z", "actor": "ci-pipeline", "commit": "4602813" },
  "steps": [
    { "name": "infra", "tool": "terraform", "collected_at": "2026-11-09T14:28:40Z",
      "outputs": { "app_gateway_public_ip": "20.61.14.7", "sql_server_fqdn": "sql-web-prd.database.windows.net",
                   "storage_account_name": "stwebprd01" },
      "sensitive_keys": ["sql_connection_string"],
      "descriptions": { "sql_server_fqdn": "SQL server host name, for connection strings",
                        "sql_connection_string": "Also stored in Key Vault kv-web-prd as secret sql-connection-string" } },
    { "name": "apps", "tool": "helm", "collected_at": "2026-11-09T14:31:12Z",
      "outputs": {}, "sensitive_keys": [], "descriptions": {} }
  ],
  "images": [
    { "module": "web", "image": "acrwebprd.azurecr.io/web/frontend", "tag": "3.14.2", "tag_source": "pin" },
    { "module": "api", "image": "acrwebprd.azurecr.io/web/api", "tag": "3.14.2", "tag_source": "pin" }
  ],
  "secrets": [
    { "key": "SQL_ADMIN_PASSWORD", "store": "azure-keyvault", "steps": ["infra"],
      "description": "SQL server admin login",
      "location": { "vault": "https://kv-web-prd.vault.azure.net/", "name": "sql-admin-password" } },
    { "key": "APPGW_CERT_NAME", "store": "azure-keyvault", "steps": ["infra"], "description": null,
      "location": { "vault": "https://kv-web-prd.vault.azure.net/", "name": "appgw-cert" } },
    { "key": "DB_PASSWORD", "store": "environment", "steps": ["infra"], "description": null,
      "location": { "name": "WEB_DB_PASSWORD" } },
    { "key": "LEGACY_API_TOKEN", "store": "constant", "steps": ["apps"], "description": null,
      "location": null, "declared_in": "environments/web-prd.yaml" }
  ]
}
```

No field holds a secret value, not even a masked one: `********` is added when the document is rendered. A minimal
PSDocs definition over that data:

```powershell
# docs/handover.Doc.ps1
Document 'Handover' {
    $d = $InputObject.data
    Title "$($d.deployment.name) — deployment handover"
    foreach ($step in $d.steps) {
        Section $step.name {
            $step.outputs.PSObject.Properties | Table -Property Name, Value
        }
    }
    Section 'Secrets' {
        $d.secrets | Table -Property key, store, @{ Name = 'Value'; Expression = { '********' } }
    }
}
```

## Remaining Work / Open Questions

None open.

## Deferred

Not part of this work. Revisit only when the trigger named for the item happens.

1. **Template documentation per provisioner.** `build run` could generate template-level documentation for each
   provisioner (PSDocs for Azure for `bicep`, `terraform-docs` for Terraform) as an optional, opt-in step that only
   runs when the tool is installed. Until then it stays outside strata, in the team's own pipeline (section 6).
   Trigger: a real team asks for it.
2. **Password Pusher integration.** Strata could push a secret to Password Pusher and return a one-time link (for
   example a `--push` option). That is its own feature, not part of `deploy report`: it reads secret values, which
   this design deliberately never does, and it needs its own decisions (where the Password Pusher instance and its
   token are configured, expiry and view limits, who may push which secret, what the audit trail records). Until
   then, strata only says where the secret is and the operator pushes it by hand (section 7). Trigger: when it is
   picked up, it gets its own work doc in `docs/work/`.
3. **Key Vault tags.** Neither a strata secret declaration nor a Terraform output has tags; both have only a
   `description`. Key Vault secrets do have tags, which can be read without the value. A convention such as a
   `strata-output = sql_connection_string` tag set by the Terraform would let strata link an output to its vault
   secret by itself. But `deploy report` would then need network access and vault permissions, and a tag
   convention to maintain, for what a description already says. Trigger: descriptions turn out not to be enough in
   practice.

## Implementation plan

0. Two `strata validate` checks, independent of the rest, can ship first: a warning for a secret that sets `version`
   (it has no effect yet; the latest version is read), and an error for an `azure-keyvault` secret whose `value` is a
   URL (a secret name is expected; use a `variable` if the address itself is wanted).
1. `StoreIntegration.location(key)` on the Key Vault and Infisical resolvers, plus built-in locations for
   `environment`, `github` and `constant`. Unit tests check that no credential or token ends up in a location.
2. `referenced_secret_keys()` next to `referenced_output_keys()` in `utils/value_tokens.py`, called on the same
   per-step input (Terraform payload and backend configuration, Helm module values, the merged Compose document,
   script `args`), plus `_referenced_keys()` for an `output.template`. A test checks that every secret a deploy
   resolves for a step is in that step's set.
3. `DeploymentOutputStore` records per step: `sensitive_keys` (names only), `descriptions` (read from the step's
   root module `.tf` files with `python-hcl2`), and the secrets the step used with their location, description and
   `declared_in`. Every step gets a record, including Helm and Compose steps, which have no outputs. Reading an older
   record without these fields still works, and a read for a document ignores `output_store_max_age`.
4. `strata deploy output`: all outputs, one step, one `STEP.KEY`, `--format raw`, `--output json`. Needs only
   step 3's outputs, sensitive names and descriptions, so it can ship before the secret work in steps 1–2.
5. `strata deploy report` with `--format table`, and `--output json`.
6. Built-in Markdown format and `--template` for `deploy report`, with the template checked by `strata validate`.
7. `spec.documentation` on the deployment, its two `strata validate` checks, and writing the document to the audit
   sink (a render failure is a warning).
8. Documentation: `docs/config/deployment.md` and a how-to for the Password Pusher handover workflow, including
   rendering the JSON envelope with PSDocs.

## Changelog

- 2026-11-09: created.
- 2026-11-09: added section 6 (PSDocs and PSDocs for Azure), open question 7 and the PSDocs how-to in step 6.
- 2026-11-09: split the flags: `--output console|json` is the command's return value, `--format table|markdown` is
  the document (the convention `values get` already follows). Renamed `--out` to `--output-path`, matching
  `promote view`. Both now follow ADR-0056.
- 2026-11-09: added section 7 (a worked example, from deploy to handover) and the rule for a Key Vault `value` that
  is a full secret URL.
- 2026-11-09: moved open question 7 (template documentation per provisioner) to a new Deferred section.
- 2026-11-09: moved open question 5 (Password Pusher integration) to Deferred; it gets its own work doc later.
- 2026-11-09: resolved the GitHub repository question: a `github` secret is described as "GitHub Actions secret,
  read from env var `NAME`", with no repository (section 2).
- 2026-11-09: resolved the `output_store` question: it is a prerequisite. Without it the command errors, with no
  fallback to `terraform output` (section 1); `strata validate` rejects `spec.documentation` without it (section 4).
- 2026-11-09: resolved "which secrets to list": only referenced ones, from `${secret:KEY}` tokens and
  `output.template` names, not `graph.secret_refs` (which holds every declared secret).
- 2026-11-09: resolved "who may see locations": the document is private, and the template author decides what a
  wider audience gets; no `--no-locations` option (section 5).
- 2026-11-09: resolved "linking a sensitive output to a vault secret": the output is listed under its own name, with
  its Terraform `description` (and a secret's own `description`), so the Terraform or environment author chooses what
  to say (section 3). Key Vault tags added to Deferred.
- 2026-11-09: review fixes. Which secrets a step used and their locations are now recorded at `deploy run`
  (`referenced_secret_keys()`), not worked out from the YAML or the local environment afterwards. Only
  Terraform/OpenTofu and GitOps steps have outputs; the example no longer shows Helm outputs. Template dynamic access
  to `secrets` counts as all secrets. Old records are shown with a warning, not refused. Section 4 has failure and
  audit-sink rules. The overview says exactly what "secret" covers, and `declared_in` follows the merge order. No
  version is shown. Open questions 1–3 (secret `version`, full Key Vault URLs, command name) need a decision.
- 2026-11-09: decided secret `version`: `strata validate` warns that it has no effect yet (implementation plan
  step 0). Open questions renumbered: full Key Vault URLs is now 1, the command name 2.
- 2026-11-09: decided the command names: two commands over the same record. `deploy output` is the `terraform
  output` equivalent (`STEP.KEY`, `--format raw`, `<sensitive>`); `deploy report` is the handover document (formerly
  `deploy output`). Not `show`: `deploy show` stays reserved for v1's resolved-config view. Open question 2 removed.
- 2026-11-09: decided Key Vault `value` is only ever a secret name; `strata validate` rejects a URL (implementation
  plan step 0). Fixed the `config/` example: `APPGW_CERT_SECRET_ID` is now a variable, because App Gateway needs the
  secret's address, not its content. No open questions left.
