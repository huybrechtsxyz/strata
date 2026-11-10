# Deployment output and report (`strata deploy output`, `strata deploy report`) — Design

- Status: current
- Last updated: 2026-11-10
- Related: [deploy-command.md](deploy-command.md) (the `deploy output` and `deploy show` rows in the v1 survey),
  [values-secrets-command.md](values-secrets-command.md) (`values list` and its `value_ref`),
  [store-integration-configuration.md](store-integration-configuration.md) (how a secret store finds its vault),
  [audit-commands.md](audit-commands.md) (the git audit sink as a durable destination),
  [how-to: deployment handover](../how-to/deployment-handover.md)

## Overview

After a deploy, the people who use what was deployed need a handover document: the endpoints, IPs, image versions
and other step outputs, plus how to get the secrets that go with them (an admin password, a connection string, an
API key). Strata provides two read commands over the same durable record, and an optional deployment setting:

- **`strata deploy output`** is the equivalent of `terraform output`, across every step of a deployment and without
  Terraform state. It lists outputs, or prints one bare value for a script.
- **`strata deploy report`** renders the handover document: outputs plus secret locations, images and who deployed
  what. Named `report`, not `show`: [deploy-command.md](deploy-command.md) keeps `deploy show` for v1's "print the
  resolved deployment config" (a pre-deploy view), and `terraform show` means full state or plan detail.
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

## Current Design

### Module layout

| Module                                       | Role                                                                                                              |
| -------------------------------------------- | ----------------------------------------------------------------------------------------------------------------- |
| `controllers/deployment_output_store.py`     | The output record: one key per step, the transition mirror, the destroyed and omitted markers (ADR-0057).         |
| `controllers/deployment_report_store.py`     | The report record per step, and the audit summary per deployment.                                                 |
| `controllers/step_secrets.py`                | Which declared secrets a step used (`step_secret_keys()`, `container_secret_keys()`).                             |
| `controllers/deployment_output_view.py`      | `deploy output`'s data: `open_output_stores()`, `read_steps()`, `status_line()`.                                  |
| `controllers/deployment_report_view.py`      | `deploy report`'s data, built on `read_steps()`, plus secret rows and images. `MASK` lives here.                  |
| `controllers/deployment_document.py`         | The one renderer, for `deploy report --format markdown` and `spec.documentation`.                                 |
| `utils/report_template.py`                   | The sandboxed Jinja2 environment and the template name check, so `strata validate` needs no report controller.    |
| `utils/terraform_outputs.py`                 | Output descriptions and declared variables from a root module's `*.tf` and `*.tf.json` files (`python-hcl2`).     |
| `templates/report/handover.md.j2`            | The built-in Markdown document, itself a template.                                                                |
| `commands/deploy_output_command.py`, `deploy_report_command.py` | The two commands.                                                                              |

### 1. Two commands over one record

Both commands are read-only. They never call a provisioner, never write infrastructure, and never read a secret
value. Both read the per-step durable records that `deploy run` writes. Both follow
[ADR-0056](../decisions/0056-output-is-the-result-format-is-the-content.md): `--output` for the run, `--format` for
the content, `--output-path` for a file.

#### `strata deploy output`: the `terraform output` equivalent

```text
strata deploy output DEPLOYMENT [STEP | STEP.KEY] [--format table|raw] [--path PATH] [--output console|json]
```

| Command                                                           | Like                          | Prints                                                    |
| ----------------------------------------------------------------- | ----------------------------- | --------------------------------------------------------- |
| `strata deploy output web-prd`                                    | `terraform output`            | Every step's outputs                                      |
| `strata deploy output web-prd infra`                              | `terraform output` in `infra` | That step's outputs                                       |
| `strata deploy output web-prd infra.sql_server_fqdn`              | `terraform output NAME`       | One output                                                |
| `strata deploy output web-prd infra.sql_server_fqdn --format raw` | `terraform output -raw NAME`  | The bare value and nothing else, for `$(...)` in a script |
| `strata deploy output web-prd --output json`                      | `terraform output -json`      | Every output in the standard envelope's `data`            |

- `STEP.KEY` is the same address as an `${output:STEP.KEY}` token, so what a person looks up is what a document
  references. The first `.` separates the step from the key.
- Terraform's `-raw` is `--format raw`, as in `values get`: ADR-0056 keeps content shapes on `--format`. `raw` needs a
  single `STEP.KEY`, otherwise it is a usage error. Under `--format raw` the run's report goes to stderr, so stdout
  carries only the value, as `terraform output -raw` does. Under `--output json`, `--format` does not apply.
- A sensitive output shows as `<sensitive>`, as `terraform output` does. One deliberate difference: `terraform
  output -raw NAME` prints a sensitive value, but strata never stores it, so `--format raw` on a sensitive output fails
  with an error that names the output and shows its description, which may say where else the value is kept
  (section 3).
- Each step's `collected_at` is shown on its status line. Descriptions are not: the table stays as short as
  Terraform's. The JSON data carries them (`steps[].outputs[]`: `name`, `value`, `sensitive`, `description`).
- Steps come from the workspace's current `execution` list, so a step removed from the configuration is not shown
  even if the store still has a record for it.
- No secret locations, images or audit information. That is `deploy report`.
- Exit codes: 2 (usage) for an unknown `STEP` or `STEP.KEY`, for `--format raw` on a sensitive output, and for a
  `STEP.KEY` in a step that has no record (including one whose outputs were too large to record). 3 (validation) when
  the workspace has no `output_store`. Listing all outputs never fails because of a missing record; that step shows
  "no outputs recorded".

Each step opens with one status line, shared by both commands (`status_line()`), for its `status`:

| `status`       | Meaning                                                                                              |
| -------------- | ---------------------------------------------------------------------------------------------------- |
| `recorded`     | Outputs recorded, with `collected_at`.                                                               |
| `not_recorded` | Never deployed with this output store.                                                               |
| `too_large`    | Its last deploy's outputs were larger than the backend accepts, so none were recorded (`omitted_at`). |
| `destroyed`    | The step was destroyed (`destroyed_at`).                                                             |

#### `strata deploy report`: the handover document

```text
strata deploy report DEPLOYMENT [--format table|markdown] [--template PATH] [--output-path FILE]
                                [--step NAME]... [--path PATH] [--output console|json]
```

- `--output console|json` (or `STRATA_OUTPUT`) is the **command's return value**, the same envelope and exit codes as
  every other command. With `--output json` and no `--output-path`, the envelope's `data` carries the structured data
  (section 7), which is also the template context. That is what a CI pipeline or another tool such as PSDocs
  consumes.
- `--format table|markdown` is the **document**: how a person reads it. `table` (the default) is console tables, the
  same style as `values list`. `markdown` is the built-in Markdown document, and `--template` replaces it with a
  Jinja2 template the user provides.
- `--output-path FILE` writes the document to a file, as `promote view` does. The document goes to the file and the
  envelope to stdout (with `--output json`, a small confirmation naming the file, not a second copy of the
  document). Without `--output-path`, a Markdown document goes to stdout and the run's report to stderr.
- `--template` and `--output-path` imply `--format markdown`; with an explicit `--format table` they are a usage
  error.
- `--pin` is not an option of `deploy report`; the end-of-deploy document (section 4) uses the `--pin` the deploy ran
  with, so its images are the ones deployed.
- Exit codes: 2 for bad arguments, an unknown `DEPLOYMENT` or `--step`, or a template that does not render; 3 when
  the solution is invalid or the workspace sets no `output_store`; 1 when the store is unreachable or a record is
  corrupt. Warnings (a secret held in source, an unknown location, an unrecorded step) still exit 0.

#### What the records hold

`deploy output` uses items 1 and 2. `deploy report` uses all four, without resolving any secret value:

1. **Step outputs** from the output record, for every execution step (or only `--step`), with each step's
   `collected_at` and each output's `description` (section 3). Only Terraform and OpenTofu steps (and GitOps steps)
   produce outputs: `collect_step_outputs()` calls the tool's `terraform output -json`, Helm has no outputs, and
   Compose steps never reach that call. A Helm or Compose step still gets a report record for the secrets it used
   (see "How Helm and Compose steps get a record" below).
2. **Sensitive output names** and descriptions for each step (section 3); their values are never stored.
3. **Secrets each step used**, each with its location and description (section 2). Only secrets a step actually
   used: a secret declared in a reachable environment but never used is left out. With `--step`, only the secrets
   those steps used are listed.
4. **Deployment context**: deployment name, workspace, environments, effective images (the `deploy images` rows), and
   the audit summary of the last successful deploy.

**Which secrets a step used** is recorded at `deploy run`, per step, not worked out from the YAML afterwards
(`controllers/step_secrets.py`). The rule is "more, never fewer":

- **Tokens.** `referenced_secret_keys()` (`utils/value_tokens.py`) scans each step's real input for `${secret:KEY}`
  tokens, on the same input `referenced_output_keys()` scans for `${output:}`
  ([output-declarations.md](output-declarations.md)): the Terraform payload and backend configuration, and script
  provisioner `args`.
- **`TF_VAR_`.** `deploy run` sets `TF_VAR_<key>` for every resolved value (`tf_var_env()`), and Terraform reads a
  `TF_VAR_` only for a declared `variable`. So a Terraform step also counts every secret whose key is a `variable` in
  its root module (`read_declared_variables()`).
- **Scripts.** A script without `script.values` gets every value in `STRATA_VALUES`, so it counts as using every
  secret (`all_secrets`).
- **GitOps.** A GitOps step counts the tokens in its remote's authentication.
- **`output.template`.** A provisioner's `output.template` receives every declared secret. The names it uses come
  from `_referenced_keys()` (`utils/templater.py`), the helper behind `validate_template_references()`. That helper
  skips dynamic access such as `secrets[name]`, so a template that accesses `secrets` dynamically counts as using
  every declared secret.

Not `graph.secret_refs`: despite its name, `build_value_references()` fills it with every secret declared in the
reachable environments, used or not.

**Two records per step, plus one per deployment.** All live in the workspace's `output_store` backend:

| Key                                         | Holds                                                                         | Written with                       |
| ------------------------------------------- | ----------------------------------------------------------------------------- | ---------------------------------- |
| `strata/step-outputs/<deployment>/<step>`   | The output record: the values a later deploy consumes (ADR-0057).             | `put()`, by the step that deployed |
| `strata/deployment-report/<deployment>/<step>` | The report record: what only `deploy output` and `deploy report` read.     | `put()`, by the step that deployed |
| `strata/deployment-audit/<deployment>`      | The audit summary of the last successful deploy.                              | `put()`, after a successful deploy |

The output record ([ADR-0057](../decisions/0057-output-record-one-key-per-step.md), which replaces ADR-0036 D3's "one
record per deployment"):

- **One key per step**, under a new prefix: on a Blob container with a hierarchical namespace the old per-deployment
  key is a file, and nothing can be created under it. Each step writes only its own key: there is nothing to merge.
- **Reads fall back to the old key** `strata/deployment-outputs/<deployment>` when a step's own key does not exist, so
  a deployment keeps working without redeploying every step. A step key holding a marker (destroyed or omitted) does
  **not** fall back: otherwise an earlier value would come back.
- **The transition mirror.** While the old key exists, `record()` also writes the step into it (compare-and-swap,
  which is why `strata validate` still requires a backend with it), so an older strata keeps reading current values.
  A deployment that never had the old key never gets one. When the old record would be too large, the step is removed
  from it instead, so an older strata finds nothing rather than an outdated value. A failed mirror is a warning. The
  fallback and the mirror are removed together in a later release, which says so in the changelog.
- **`clear()`** writes the destroyed marker `{"destroyed_at": ...}` to each step key that exists and removes the step
  from the old key, as before. The key-value interface has no delete.
- **Staleness:** each step key carries its own `collected_at`, and `output_store_max_age` applies per step.

The report record:

- **Written once per step, after the output record, by that step, with a plain `put()`.** Each key has one writer,
  and a lost race only loses document metadata, never a value a deploy consumes. Never under `--dry-run`.
- **It holds:** the step's `collected_at` (the same timestamp as its output record), the names of its sensitive
  outputs, output descriptions, and the secrets it used (key, store, location, description, `declared_in`), or
  `all_secrets`. Never a value, sensitive or not. It always writes `secrets` (an empty list when none), so a record
  without the field reads as "secrets not recorded", never as "no secrets". A step whose `.tf` files declare no
  outputs still gets a record.
- **A failed write is a warning, not a failed deploy.** The infrastructure is deployed and the output record is
  written; only the document is missing information, which the next deploy of that step fills in.
- **Read:** one `get()` of each record per step in the deployment's step list (`read_steps()`). No listing, which the
  key-value interface does not have.
- **Destroy** writes the destroyed marker, and the report lists that step as destroyed.

**Report state.** Both records of one deploy carry the same `collected_at`, so a report record from another deploy is
recognised. Its sensitive names and secrets are then not shown, with a warning (`report_secrets_not_recorded`),
rather than shown out of date. `StepOutputs.report` and `ReportStep.secrets` take these values:

| State          | Meaning                                                                                            |
| -------------- | -------------------------------------------------------------------------------------------------- |
| `recorded`     | From the same deploy as the outputs.                                                               |
| `not_recorded` | No report record: an earlier strata deployed the step, or the write failed.                        |
| `mismatch`     | From a different deploy: an earlier one (the report write failed) or a later one (the output write failed). |
| `destroyed`    | The step was destroyed.                                                                            |

**The audit summary** is `deployed_by`, `completed_at`, `status`, `action`, `version`, `execution_id` and
`commit_sha`, every key always present (`null` where unknown, such as `commit_sha` outside git), so a template can
rely on it. `deploy run` writes it from that run's audit manifest after every successful deploy, so `deploy report`
shows it without reading the git audit sink. `commit_sha` comes from `built_commit_sha()`: the CI commit variable,
else `git rev-parse HEAD`.

**Size.** The limit per key depends on the backend, declared as `MAX_VALUE_BYTES` on `KeyValueStoreIntegration`. The
size measured is the key plus the serialised value, because App Configuration counts the key too.

| Backend           | `MAX_VALUE_BYTES` | Limit per key                                                                                                         |
| ----------------- | ----------------- | --------------------------------------------------------------------------------------------------------------------- |
| `azure-appconfig` | 10,000            | 10 KB including key, label, content type and tags; every tier ([source](https://learn.microsoft.com/en-us/azure/azure-app-configuration/concept-key-value)) |
| `azure-keyvault`  | 25,000            | 25 KB per secret value                                                                                                |
| `azure-blob`      | `None`            | 5,000 MiB in one `Put Blob` ([source](https://learn.microsoft.com/en-us/rest/api/storageservices/put-blob))           |

Per step, 10 KB is a lot: 30 outputs with 100-character descriptions and 15 secret rows come to about 8 KB of compact
JSON. Both records stay small: compact JSON with short field names and empty fields left out, no values repeated in
the report record, sensitive outputs as a list of names, and a secret's location as structured fields rather than a
rendered sentence. No compression: it would roughly halve the size, but the record would no longer be readable in the
Azure portal.

Before each write, the record is measured against the backend's limit:

- **Above 8 KB:** a warning naming the deployment, the step, the record and its size (code `output_store_record`).
  Only on a backend with a limit.
- **Above the limit, report record:** descriptions are shortened to 200 characters and the step is marked
  `"truncated": true`, which the report shows. If it is still too large, descriptions are dropped. Names and
  locations are always kept: those are what the handover needs.
- **Above the limit, output record:** values cannot be shortened. The step's key gets the omitted marker
  `{"omitted_at": ..., "size": ...}`, the step is removed from the old key, and the deploy reports a warning naming
  the step, its size and the backend's limit (`OutputRecordTooLargeError`). A reader then gets
  `OutputRecordOmittedError` (status `too_large`) instead of an earlier deploy's values; a later `${output:}` to that
  step fails as for any missing record.

**Estimating the size before a deploy.** `strata build run` does it, not `strata validate`:

- `validate` reads only the YAML. It renders no Terraform and fetches no modules, so it has neither output names nor
  their descriptions.
- `build run` has the rendered step: the `.tf` files, the step's payload with its `${secret:}` tokens, Helm module
  values, and the merged `docker-compose.yml`. Locations come from the configuration. So it computes the report record
  with the same code the deploy uses (only `collected_at` is missing) and gives the same warnings. Secrets for
  non-container steps are counted from the whole strata payload rather than the step's claimed part, so the estimate
  can only over-count. Container steps are estimated after the workload pipeline, with the same scan as the deploy.
- When the output store cannot be resolved where the build runs, the estimate is skipped with a warning
  (`report_estimate_skipped`); the build itself is unaffected.
- The **output record** cannot be sized before the apply: output values only exist after it, and one output (a
  kubeconfig, a certificate) can be several KB on its own. The real check is the one at deploy time.

**How Helm and Compose steps get a record.** Container steps take their own branch in `deploy_controller.py`: per
namespace, `check_namespace()` and `deploy_namespace()`. Their secret tokens are resolved inside the integration, so
the controller never sees which ones a step used. Container steps only need a report record (they have no outputs):

- The container integration base class (`capabilities.py`) has `referenced_secret_keys(namespace, modules) ->
  set[str] | None`. Helm scans `_render_values(module)` for each module, the same input its `${output:}` pre-flight
  scans. Compose scans the namespace's merged `docker-compose.yml`. `None` means the tool cannot tell, and the step
  then counts as using every declared secret.
- `container_secret_keys()` runs that scan once, before the namespaces deploy and only outside `--dry-run` with an
  output store; `build run`'s estimate uses the same function. After every namespace deployed without an error, the
  controller writes the step's report record. The output record is not touched. The call goes from controller to
  integration, which ADR-0003 allows.
- A failed step records nothing: Helm reports a skipped module as an error diagnostic, so the controller returns
  before recording, as the Terraform path does.
- Compose never rewrites the built `docker-compose.yml`. Each deploy resolves it into `docker-compose.deploy.yml`
  next to it (Docker resolves relative paths from the compose file's folder) and deploys that, with each secret as
  `${KEY}` and its value in the `docker stack` process environment. So a second deploy of one build sees the same
  tokens as the first.

**Prerequisite: the workspace sets `output_store`.** The durable records are the only source of step outputs and
secret locations. Without it, both commands stop with an error that names the missing setting
(`output_store_missing`, exit 3). Reading needs network access and credentials for the store's own backend, like any
other `output_store` read. What the commands never need is provider credentials or Terraform state: they do not fall
back to `terraform output -json` against the build path.

**Old records are shown, not refused.** `DeploymentOutputStore.read()` raises `OutputRecordStaleError` when a record
is older than the workspace's `output_store_max_age`, because a deploy must not consume a stale value. A person
reading values is different: both commands read without that limit and show each step's `collected_at`, with a
warning for a step older than `output_store_max_age`.

Why a command rather than an execution step: a provisioner step changes infrastructure and has plan, apply and
destroy behaviour, which a document does not. A step would also only see the outputs from steps that ran in that
invocation, so a `--stage` or `--scope` run would give an incomplete document. The command reads the durable record,
so it can run again at any time, in its own CI job, without redeploying.

### 2. Secret locations instead of values

Every secret row carries a `location` that says where to find the secret, and `where`, the same location rendered as
one line (`render_location()`). Connection settings only: no credentials, no tokens.

The location is **recorded at `deploy run`**, in the step's report record, not worked out when the document is
rendered. Several connection settings come from environment variables (`AZURE_KEYVAULT_URL`, `INFISICAL_ADDR`,
`INFISICAL_PROJECT_ID`, `INFISICAL_ENVIRONMENT`) that are often set only on the CI runner. Worked out on a laptop,
the location would be `unknown`, or wrong if those variables point somewhere else. Recorded at deploy time, it is
where the deploy actually read the secret.

Locations come from `StoreIntegration.location(key)` on a resolver constructed exactly as `resolve_values()`
constructs it (`value_controller.secret_records()`), plus built-in locations for `environment`, `github` and
`constant`. Nothing is contacted, and strata does not rebuild the resolver's logic elsewhere, so the location cannot
drift from what the deploy read.

| `store`              | `location` fields                                         | `where`                                                                         |
| -------------------- | --------------------------------------------------------- | ------------------------------------------------------------------------------- |
| `azure-keyvault`     | `vault` (URL), `name` (`value`)                           | `kv-prd-weu.vault.azure.net / sql-admin-password (latest)`                      |
| `infisical`          | `address`, `project_id`, `environment`, `path`, `name`    | `https://app.infisical.com / project a1b2… / env prod / / / SQL_ADMIN_PASSWORD` |
| `github`             | `env` (the env var the workflow maps the secret into)     | `GitHub Actions secret, read from env var SQL_ADMIN_PASSWORD; see the workflow for its source` |
| `environment`        | `env` (the env var)                                       | `env var SQL_ADMIN_PASSWORD (set on the deploy runner)`                         |
| `vault`, `bitwarden` | `name` (`value`) only, since no resolver says which server | `secret/data/prd/sql`                                                          |
| `constant`           | **none.** `value_ref` is the secret, so it is never shown | `held in source (environments/prd.yaml), move to a vault`                       |

Rules:

- The `value` field is always `********`, for every store, including an empty or unresolvable secret. Neither the two
  commands nor the recording step reads a secret value to build a location, so neither can leak one.
- **No version is shown**, because none is used: the resolvers always read the latest version. `SecretStoreModel`
  accepts `version`, but `resolve_values()` never passes it on. `strata validate` warns on any secret that sets
  `version`, saying it has no effect yet. If a resolver later honours `version`, the location shows it and that
  resolver's warning goes away.
- **Which document declares a secret** (`declared_in`): `merge_environment_models()` keeps only the winning
  declaration, not where it came from. The recording step works it out from the same merge order: the last reachable
  environment that declares the key wins, so that is the one named.
- A `store: constant` secret gets a warning (`secret_held_in_source`): a secret held in source cannot be handed over
  by location, and should probably move to a vault.
- A setting that was not set where the deploy ran is `null` in the record and `unknown` in the document, with a
  warning (`secret_location_unknown`), not an error. The document is still useful without it.
- **A Key Vault `value` is only ever a secret name.** The store and its integration say which vault. `strata validate`
  rejects a URL as the `value` of an `azure-keyvault` secret, and suggests a `variable` when the address itself is
  what is wanted (App Gateway's `key_vault_secret_id`, for example).
- A `github` secret is described by its environment variable and nothing more. Strata only reads the variable; the
  workflow maps the GitHub secret into it (`env: NAME: ${{ secrets.OTHER_NAME }}`). Strata cannot know which
  repository, GitHub environment or organization holds the secret, nor whether it has the same name, so the document
  does not guess.

### 3. Sensitive step outputs

A Terraform output marked `sensitive = true` is never written to the output record. Its **name** is kept in the
report record (`sensitive_keys`), so the document lists it with `********` as its value.

Strata does not know where else a sensitive value lives, so it does not say. Instead the document shows the output's
own Terraform `description`, which leaves the choice to whoever writes the Terraform. If the step also writes the
value to a vault (for example an `azurerm_key_vault_secret` resource), the description can say so:

```hcl
output "sql_connection_string" {
  value       = "Server=tcp:..."
  sensitive   = true
  description = "Also stored in Key Vault kv-web-prd as secret sql-connection-string"
}
```

An output without a description shows only its name and `********`. Nothing in strata's schema links an output to a
vault secret; the description is free text, for people.

Descriptions apply to every output, not just sensitive ones, and are recorded at `deploy run` time. `terraform output
-json` does not return them (only `value`, `type` and `sensitive`), so strata reads them from the step's root module
`*.tf` and `*.tf.json` files with `python-hcl2` (`utils/terraform_outputs.py`). Child modules are not read, and a
description that is an expression is kept as written. A description is source code, not a secret, so it is stored
even for a sensitive output. A later `deploy output` or `deploy report` in another CI job then needs neither the build
directory nor Terraform.

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

At the end of a successful `deploy run` (not a destroy, not a dry run), strata runs the same render as `deploy report
--format markdown`, with the deploy's `--pin`, and writes `_handover.md` into the audit record's directory, so each
deploy leaves a dated handover document in the audit remote.

- `strata validate` rejects `spec.documentation` when the workspace has no `output_store`, or when `sink: audit` is
  set and no git audit sink is configured. Both gaps show up before a deploy, not at the end of one. Only a git sink
  can carry the document: it pushes a dict of files (`_manifest.json`, and `_metrics.json` when admitted,
  `audit_run.py`), so the document is one more entry. An integration sink sends events, not files.
- Rendering runs after every step has applied. If anything fails (a template error, the store, the audit push),
  `deploy run` reports a warning and still exits 0: the infrastructure is deployed, and failing the run would invite a
  redeploy that changes nothing. `strata deploy report` can render the document again afterwards.

### 5. Templates

- One renderer (`controllers/deployment_document.py`) serves `deploy report --format markdown` and
  `spec.documentation`. The built-in Markdown is itself a template (`templates/report/handover.md.j2`).
- Templates run in Jinja2's `SandboxedEnvironment` with `StrictUndefined`. They receive the `deploy report --output
  json` data (section 7) plus `generated_at`, `MASK` and an `md_cell` filter for table cells.
- There is no variable in that context that holds a secret value, so a template cannot print one even by mistake.
  `secrets[].value` is `********`, not absent.
- A template is code: any error it raises at render time is a template error (`DocumentTemplateError`, exit 2 from
  `deploy report`, a warning at the end of a deploy).
- **The document is private.** It is meant for the team that runs the deployment, like the audit record it is
  stored next to. Secret locations (vault URL, secret name), IPs and hostnames are not secrets, but they do tell a
  reader where to look. The built-in Markdown includes them. Whoever writes a template decides what goes into a
  document for a wider audience, for example by leaving out `secrets[].where`. Strata has no `--no-locations` option:
  the one guarantee it gives is that no secret value can be printed.
- `strata validate` checks a `spec.documentation.template` with the same name check as
  `validate_template_references()`, so a typo fails before a deploy, not after.

### 6. PSDocs and PSDocs for Azure (PowerShell)

Teams on Azure may already use [PSDocs](https://github.com/microsoft/PSDocs), a PowerShell engine that generates
Markdown from objects using document definitions (`*.Doc.ps1`), and its module
[PSDocs for Azure](https://azure.github.io/PSDocs.Azure/overview), which generates Markdown from ARM templates. They
cover two different things:

| Tool             | Documents                                                        | When          | Fit with strata                                                                                               |
| ---------------- | ---------------------------------------------------------------- | ------------- | ------------------------------------------------------------------------------------------------------------- |
| PSDocs           | Any object, rendered by a PowerShell document definition         | After deploy  | An alternative to the Jinja2 template: it renders the `deploy report --output json` envelope.                 |
| PSDocs for Azure | An ARM template: what it takes and produces, not deployed values | Before deploy | Documents the template a `bicep` provisioner deploys. Complements the handover document, does not replace it. |

**PSDocs as a renderer needs no strata code.** The JSON envelope is the contract:

```powershell
Import-Module PSDocs
strata deploy report prd-deployment --output json |
    ConvertFrom-Json |
    Invoke-PSDocument -Path ./docs/handover.Doc.ps1 -OutputPath ./out/docs
```

The secret rule still holds, because the envelope has no secret values for a PowerShell definition to print either.
The how-to ([deployment-handover.md](../how-to/deployment-handover.md)) walks through it.

**PowerShell is not a dependency of strata.** Strata is a cross-platform Python CLI, and `pwsh` is not on every
runner. Running PSDocs for Azure (or `terraform-docs`) during `build run` is out of scope; see History.

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
infra  collected 2026-11-09T14:28:40+00:00
  app_gateway_public_ip  20.61.14.7
  sql_connection_string  <sensitive>
  sql_server_fqdn        sql-web-prd.database.windows.net
apps  no outputs recorded

$ strata deploy output web-prd infra.sql_server_fqdn --format raw
sql-web-prd.database.windows.net
```

`apps` is a Helm step: it has a report record (the secret it used) but no output record. `--format raw` on `infra.sql_connection_string` fails (exit 2): the output is sensitive, strata never stores its
value, and the error shows its description. In a later CI job:
`$fqdn = strata deploy output web-prd infra.sql_server_fqdn --format raw`.

**Rendering the document in CI**, the job after `deploy run`:

```powershell
$env:STRATA_OUTPUT = "json"
strata deploy report web-prd --format markdown --output-path out/web-prd-handover.md
```

Because `--output-path` is set, the document goes to the file and stdout carries only the envelope: its `data` names
the file written, and its diagnostics include the `secret_held_in_source` warning for `LEGACY_API_TOKEN`.

**The generated document**, `out/web-prd-handover.md`, from the built-in template:

````markdown
# web-prd — deployment handover

Deployed 2026-11-09T14:32:05+00:00 by `ci-pipeline` from commit `4602813`, audit record `3f2c9a1e-5b7d-4e8a-9c0f-1d2e3f4a5b6c`.
Workspace `web`, environments `web-prd`.
Generated 2026-11-10T08:00:00+00:00 from the outputs recorded by `deploy run`: a snapshot, not a live view.

## Step outputs

### infra

Collected 2026-11-09T14:28:40+00:00.

| Output | Value | Description |
| ------ | ----- | ----------- |
| app_gateway_public_ip | 20.61.14.7 |  |
| sql_connection_string | ******** | Also stored in Key Vault kv-web-prd as secret sql-connection-string |
| sql_server_fqdn | sql-web-prd.database.windows.net | SQL server host name, for connection strings |

### apps

No outputs recorded.

## Secrets

Values are never included. Ask the operator for a one-time link, or fetch the secret from the location below if you
have access.

| Secret | Value | Store | Where to find it | Used by | Description |
| ------ | ----- | ----- | ---------------- | ------- | ----------- |
| APPGW_CERT_NAME | ******** | azure-keyvault | kv-web-prd.vault.azure.net / appgw-cert (latest) | infra |  |
| DB_PASSWORD | ******** | environment | env var WEB_DB_PASSWORD (set on the deploy runner) | infra |  |
| LEGACY_API_TOKEN | ******** | constant | held in source (environments/web-prd.yaml), move to a vault | apps |  |
| SQL_ADMIN_PASSWORD | ******** | azure-keyvault | kv-web-prd.vault.azure.net / sql-admin-password (latest) | infra | SQL server admin login |

## Images

| Name | Image | Tag | Source |
| ---- | ----- | --- | ------ |
| web | acrwebprd.azurecr.io/web/frontend | 3.14.2 | pin |
````

**Handing a secret over.** The reader asks for the SQL admin password. The operator, who has read access to the
vault, uses the location from the document:

```powershell
az keyvault secret show --vault-name kv-web-prd --name sql-admin-password --query value -o tsv
```

and pastes the value into Password Pusher with a short expiry and a single view, then sends the link. Strata never
reads the value, and the document never contains it. For `sql_connection_string`, the operator follows the output's
description to secret `sql-connection-string` in the same vault.

**The same data for PSDocs or a template.** Without `--output-path`, `--output json` puts the full data in the
envelope (abridged: one output, one secret, one image):

```json
"data": {
  "deployment": "web-prd",
  "workspace": "web",
  "environments": ["web-prd"],
  "steps": [
    { "step": "infra", "status": "recorded", "collected_at": "2026-11-09T14:28:40+00:00",
      "destroyed_at": null, "omitted_at": null, "report": "recorded", "truncated": false,
      "outputs": [
        { "name": "sql_connection_string", "value": null, "sensitive": true,
          "description": "Also stored in Key Vault kv-web-prd as secret sql-connection-string" }
      ],
      "secrets": "recorded", "all_secrets": false,
      "secret_keys": ["APPGW_CERT_NAME", "DB_PASSWORD", "SQL_ADMIN_PASSWORD"] }
  ],
  "secrets": [
    { "key": "SQL_ADMIN_PASSWORD", "store": "azure-keyvault",
      "location": { "vault": "https://kv-web-prd.vault.azure.net/", "name": "sql-admin-password" },
      "where": "kv-web-prd.vault.azure.net / sql-admin-password (latest)",
      "description": "SQL server admin login", "declared_in": "environments/web-prd.yaml",
      "used_by": ["infra"], "value": "********" }
  ],
  "images": [
    { "name": "web", "image_name": "acrwebprd.azurecr.io/web/frontend", "image_tag": "3.14.2",
      "tag_source": "pin", "...": "the other `deploy images` fields" }
  ],
  "audit": { "deployed_by": "ci-pipeline", "completed_at": "2026-11-09T14:32:05+00:00", "status": "success",
             "action": "deploy", "version": null, "execution_id": "3f2c9a1e-5b7d-4e8a-9c0f-1d2e3f4a5b6c",
             "commit_sha": "4602813a…" }
}
```

A sensitive output's `value` is `null` and a secret's is `********`: no field holds a secret value, not even a masked
one. A minimal PSDocs definition over that data:

```powershell
# docs/handover.Doc.ps1
Document 'Handover' {
    $d = $InputObject.data
    Title "$($d.deployment) — deployment handover"
    foreach ($step in $d.steps) {
        Section $step.step {
            $step.outputs | Table -Property name, @{ Name = 'Value'; Expression = { if ($_.sensitive) { '********' } else { $_.value } } }
        }
    }
    Section 'Secrets' {
        $d.secrets | Table -Property key, store, where, value
    }
}
```

Out of scope (each revisited only when its trigger happens):

- **Template documentation per provisioner** (PSDocs for Azure for `bicep`, `terraform-docs` for Terraform) as an
  opt-in `build run` step. Until then it stays in the team's own pipeline. Trigger: a real team asks for it.
- **A Password Pusher integration** (a `--push` option returning a one-time link). It reads secret values, which this
  design never does, and needs its own decisions (instance and token configuration, expiry and view limits, who may
  push what, the audit trail). Trigger: when picked up, it gets its own work doc.
- **Key Vault tags** linking an output to its vault secret (a `strata-output = NAME` tag). `deploy report` would then
  need network access, vault permissions and a tag convention, for what a description already says. Trigger:
  descriptions turn out not to be enough in practice.

## Related Decisions

- [ADR-0036](../decisions/0036-cross-run-key-value-output-store.md) — the key-value output store
  (`DeploymentOutputStore`); its D3 is partly replaced by ADR-0057.
- [ADR-0056](../decisions/0056-output-is-the-result-format-is-the-content.md) — `--output` is the run's result,
  `--format` the content, `--output-path` a file.
- [ADR-0057](../decisions/0057-output-record-one-key-per-step.md) — the output record moves to one key per step, with
  a read fallback and a transition mirror into the old key, and the omitted marker above the size limit.
- [ADR-0003](../decisions/0003-provider-model-design-decisions.md) — controllers may call integrations (the
  container secret scan).

## History

- **Sensitive output names used to be dropped.** Before this work the output record filtered sensitive outputs out
  entirely, names included, so a document could not even say one existed. The report record keeps the names, never
  the values.
- **Which secrets a step used is recorded at deploy, not derived from YAML.** A YAML walk would have to know exactly
  which documents each tool reads, and a document it missed would drop a secret silently. The first design also
  missed the `TF_VAR_` path (every resolved value is set as `TF_VAR_<key>`), found while building; the rule became
  "more, never fewer".
- **Locations are recorded at deploy, not at render.** The connection settings are often environment variables set
  only on the CI runner; worked out on a laptop they would be `unknown` or wrong.
- **A Key Vault URL as `value` was tested, not assumed.** With a capturing transport, a full URL gets URL-encoded
  into the secret name (`…/secrets/https%3A%2F%2Fkv-example…`) and can never match. Hence the validate error, and
  `config/`'s `APPGW_CERT_SECRET_ID` became a `store: constant` variable used as `${var:...}`: App Gateway needs the
  secret's address, not its content.
- **No version in a location**, because `resolve_values()` never passes `version` on; showing a pinned version would
  claim something the deploy did not do. Hence the validate warning instead.
- **Two commands, not one.** `deploy output` matches what a DevOps engineer expects from the name (`terraform
  output`: `STEP.KEY`, `--format raw`, `<sensitive>`); the handover document became `deploy report`. Not `deploy
  show`, which stays reserved for v1's resolved-config view.
- **The report data is its own record**, written with a plain `put()`: each key has one writer and a lost race loses
  only document metadata. Key Vault was considered as the report store, but only as a comparison; the report uses the
  `output_store` backend like everything else.
- **The output record moved to one key per step (ADR-0057)** after checking App Configuration's 10 KB limit, which
  the old one-record-per-deployment layout was already exposed to with no size check. The new prefix
  (`strata/step-outputs/`) exists because a hierarchical-namespace Blob container cannot hold keys under the old
  key's file. Of the options for older strata versions, mirroring into the old key while it exists was chosen.
- **Sizing happens in `build run`**, because `validate` has no rendered Terraform and no module sources; the output
  record can only be sized at deploy.
- **Above the limit, an output record leaves an omitted marker** rather than nothing. Leaving the previous key in
  place would hand the next reader, and the next deploy's `${output:}`, an earlier deploy's values.
- **A report record from another deploy is a `mismatch`, whichever direction.** It was first called `older`, but a
  failed output write leaves a report record that is newer than the outputs, so the comparison is `!=`.
- **The audit summary always has every key.** A deploy outside git has no `commit_sha`, and `StrictUndefined` made
  the built-in template fail on the missing key. The summary is read from the output store rather than the git audit
  sink, which is why the early `--with-audit` option was removed.
- **Compose used to rewrite `docker-compose.yml` in place**, so a second deploy of the same build (after `--dry-run`,
  or a retry) found no `${secret:}` token left, deployed every secret as an empty string (Docker only warns), and
  reused the first run's values. Each deploy now writes `docker-compose.deploy.yml` beside the built file.
- **PowerShell stays out of strata.** PSDocs works over the JSON envelope with no strata code; running PSDocs for
  Azure inside `build run` would make `pwsh` a dependency on every runner for a `bicep`-only benefit.
