# Integration (`kind: integration`)

A connection to an external tool or service — Terraform, Helm, Compose today; Vault, Azure Key
Vault, git, or a custom plugin as capability-backed stores grow. A standalone kind (unlike v1,
which embedded it as a bare list on `Configuration`) so each integration is independently
authorable and reusable, referenced by name from a [`provisioner`](workspace.md) or a
[`variable`/`secret`/`feature` store](environment.md).

## Schema

- `spec.type` — the specific tool/service (`terraform`, `vault`, `azure-keyvault`, `bitwarden`,
  `git`, or a custom plugin name) — an open string, not a closed enum
- `spec.capabilities` — what this integration provides: `variables`, `secrets`, `features`,
  `infrastructure`, `container`, `sources`, or an `x-`-prefixed extension strata doesn't dispatch on
- `spec.required` — whether this integration must be available for platform operation (default `false`)
- `spec.enabled` — whether it's active (default `true`)
- `spec.authentication` — same shape as [provider.md](provider.md)'s
- `spec.transport` — how it's reached (`cli`, `http`, `sdk`, ...) — checked against the resolved
  class's own supported transports at runtime, not schema time
- `spec.version` — an expected version constraint (e.g. `>= 1.7`), checked at preflight
- `spec.command` — override the class's default executable
- `spec.endpoints` — address, for a networked transport
- `spec.lifecycle` — hook scripts (`.py` only)

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: integration
meta:
  name: terraform
spec:
  type: terraform
  capabilities: [infrastructure]
  description: "Terraform CLI — provisions the terraform-tool workspaces in this solution"
  required: true
  enabled: true
  version: ">= 1.7"
```

## Notes

- A [`provisioner`](workspace.md)'s `integration` field is optional — if unset, resolution
  auto-binds to the sole compatible registered integration and errors (rather than guessing) if
  more than one candidate exists.
- Version/transport/authentication are owned here, once — v1 modelled the same fact in three
  places (`IntegrationModel.validation`, `WorkspaceIacModel.version`, the tool binding itself)
  with nothing arbitrating between them.
