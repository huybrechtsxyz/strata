# How To: Hand Over a Deployment Without Sharing Secrets

After a deploy, someone needs to know what was built and how to reach it: host names, IPs, which secrets the
deployment uses and where each one lives. This guide produces that handover document with
`strata deploy report`, stores it with each deploy's audit record, and hands a secret over with a one-time link.

strata never reads a secret value for this. Every secret, and every sensitive Terraform output, shows as
`********`. The document says **where** a secret is (vault and name, environment variable); whoever holds access
fetches it and passes it on.

The design is in [deployment-documentation.md](../design/deployment-documentation.md).

## 1. Prerequisite: an output store

Both `deploy output` and `deploy report` read what `deploy run` recorded in the workspace's `output_store`, so set
one ([workspace.md](../config/workspace.md#output-store)):

```yaml
kind: workspace
spec:
  output_store: azure-blob        # or azure-appconfig
```

Then deploy as usual. After each step, `deploy run` records its outputs, the names of its sensitive outputs, each
output's `description`, and the secrets the step used, each with its location. A step deployed before this was set
shows "no outputs recorded"; deploy it again.

Give outputs and secrets a description: it is what the reader sees next to them.

```hcl
output "sql_connection_string" {
  value       = "Server=tcp:..."
  sensitive   = true
  description = "Also stored in Key Vault kv-web-prd as secret sql-connection-string"
}
```

```yaml
# environments/web-prd.yaml
spec:
  secrets:
    - key: SQL_ADMIN_PASSWORD
      store: azure-keyvault
      value: sql-admin-password
      description: SQL server admin login
```

## 2. Look at it

```bash
strata deploy output web-prd                         # like `terraform output`
strata deploy output web-prd infra.sql_server_fqdn --format raw
strata deploy report web-prd                          # outputs, secrets by location, images
```

`deploy report` lists only the secrets a step used. A `constant` secret is listed as held in source, with a warning:
it cannot be handed over by location, so move it to a vault.

## 3. Write the document

```bash
strata deploy report web-prd --format markdown --output-path out/web-prd-handover.md
```

The document opens with the last successful deploy: who ran it, when, from which commit, and its audit record.
`deploy run` keeps that in the output store, so no access to the git audit sink is needed. Use `--template` to
replace the built-in Markdown with your own Jinja2 template. A template receives the same data as
`strata deploy report web-prd --output json` (`deployment`, `workspace`, `environments`, `steps`, `secrets`,
`images`, `audit`, `generated_at`, and `MASK`), and none of it holds a secret value, so a template cannot print one.
The `md_cell` filter keeps a value inside one Markdown table cell.

```jinja
# {{ deployment }}
{% for s in secrets %}
- {{ s.key }}: {{ s.where }}{% if s.description %} ({{ s.description }}){% endif %}
{% endfor %}
```

The document is private: it is meant for the team that runs the deployment. Locations, IPs and host names are not
secrets, but they tell a reader where to look. For a wider audience, write a template that leaves them out.

## 4. Store one with every deploy

Set `spec.documentation` on the deployment ([deployment.md](../config/deployment.md#handover-document)):

```yaml
kind: deployment
spec:
  documentation:
    sink: audit
    template: docs/handover.md.j2   # optional
```

After every successful `deploy run`, the document is written as `_handover.md` next to `_manifest.json` and pushed
with it by every enabled git audit sink, with that run's audit entry at the top. `strata validate` rejects
`spec.documentation` without an `output_store`, without an enabled git audit sink, or with a template that does not
exist or uses a name the report does not have. If rendering fails at the end of a deploy, the deploy still succeeds
with a warning (`documentation_render_failed`); `strata deploy report` renders it again.

## 5. Hand a secret over with Password Pusher

A reader asks for the SQL admin password. The operator, who has read access to the vault, takes the location from the
document:

| Secret             | Value    | Store          | Where to find it                                  |
| ------------------ | -------- | -------------- | ------------------------------------------------- |
| SQL_ADMIN_PASSWORD | ******** | azure-keyvault | kv-web-prd.vault.azure.net / sql-admin-password (latest) |

fetches the value:

```powershell
az keyvault secret show --vault-name kv-web-prd --name sql-admin-password --query value -o tsv
```

and pastes it into [Password Pusher](https://pwpush.com) (or the team's own instance) with a short expiry and a
single view, then sends the link. The value is never in the document, the audit record or a chat message. For a
sensitive output such as `sql_connection_string`, follow its description to where else it is stored.

## 6. Render it with PSDocs instead

Teams that already use [PSDocs](https://github.com/microsoft/PSDocs) can render the JSON envelope with a PowerShell
document definition. No strata change is needed: the envelope is the contract, and it holds no secret values.

```powershell
Import-Module PSDocs
strata deploy report web-prd --output json |
    ConvertFrom-Json |
    Invoke-PSDocument -Path ./docs/handover.Doc.ps1 -OutputPath ./out/docs
```

```powershell
# docs/handover.Doc.ps1
Document 'Handover' {
    $d = $InputObject.data
    Title "$($d.deployment) - deployment handover"
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

PSDocs for Azure documents an ARM or Bicep template (what it takes and produces) before a deploy. It complements this
document; it does not replace it.
