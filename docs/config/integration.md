# Integration (`kind: integration`)

A connection to an external tool or service — Terraform, Helm, Compose today; Vault, Azure Key
Vault, git, or a custom plugin as capability-backed stores grow. A standalone kind (unlike v1,
which embedded it as a bare list on `Configuration`) so each integration is independently
authorable and reusable — referenced **by name** from a [`provisioner`](workspace.md) (its
optional `integration:` field), or found automatically **by type** for a
[`variable`/`secret`/`feature` store](environment.md)'s `store:` field, which has no field to name
one by at all. See [docs/integrations/](../integrations/README.md) for a configuration guide per
real integration (Infisical, Azure Key Vault, Azure App Configuration).

## Schema

- `spec.type` — the specific tool/service (`terraform`, `vault`, `azure-keyvault`, `bitwarden`,
  `git`, or a custom plugin name) — an open string, not a closed enum
- `spec.capabilities` — what this integration provides: `variables`, `secrets`, `features`,
  `infrastructure`, `container`, `sources`, or an `x-`-prefixed extension strata doesn't dispatch on.
  `sources` is what a remote in `strata.yaml` or an asset's `spec.location` needs from its
  `integration`: credentials for cloning a private git repository (see
  [integrations/git.md](../integrations/git.md)) or pulling a chart from a private chart registry (see
  [integrations/chart-registries.md](../integrations/chart-registries.md)).
  `release_gate` (a platform's pass/fail verdict on a repository at a commit) and `image_findings`
  (a platform's open issues for a container image) back the workspace's `release_gate_policy` and
  `image_gate_policy` (see [how-to/security-gates.md](../how-to/security-gates.md)); `type: aikido`
  provides both, one document per credential
- `spec.configuration` — integration-specific settings, validated by the integration itself (an
  unknown key is an error, not silently ignored)
- `spec.required` — whether this integration must be available for platform operation (default `false`)
- `spec.enabled` — whether it's active (default `true`)
- `spec.authentication` — same shape as [provider.md](provider.md)'s
- `spec.transport` — how it's reached (`cli`, `http`, `sdk`, ...) — checked against the resolved
  class's own supported transports at runtime, not schema time
- `spec.version` — the version the local tool must satisfy: a PEP 440 constraint (`>= 1.7`,
  `~= 1.9`, `>= 1.7, < 2.0`) or Terraform's `~> 1.9` (read as `~= 1.9`). Checked by every `strata
  deploy` command before any step runs (not under `--smoke-test`, which never runs a tool). Only for
  CLI tools: on a networked integration (e.g. `azure-keyvault`, `aikido`) it is a validation error,
  since strata cannot read a remote service's version
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

An integration that authenticates cloning private git repositories (`authentication` names keys of
secrets, never values):

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: integration
meta:
  name: git-customers
spec:
  type: git
  capabilities: [sources]
  authentication:
    method: token
    token:
      token: CUSTOMER_GIT_TOKEN
      username: build-bot
```

## Notes

- A [`provisioner`](workspace.md)'s `integration` field is optional — if unset, resolution
  auto-binds to the sole compatible registered integration and errors (rather than guessing) if
  more than one candidate exists.
- Version/transport/authentication are owned here, once — v1 modelled the same fact in three
  places (`IntegrationModel.validation`, `WorkspaceIacModel.version`, the tool binding itself)
  with nothing arbitrating between them.
- `strata integration check NAME [--deployment D]` changes nothing. It first checks local readiness,
  the same check every `strata deploy` command runs before any step: enabled, tool on PATH,
  installed version against `spec.version`. For a type with a connection check (`aikido` today) it
  then verifies credentials and reachability; with `--deployment`, the `authentication` key
  references resolve through that deployment. NAME is a document name or an integration type
  (`strata integration check terraform` checks what a `tool: terraform` provisioner would bind).
  Exit 0 ready, 1 unreachable, 2 unknown name / ambiguous type / `--deployment` needed, 3 not
  ready locally or credentials missing or rejected.
- `strata integration list [--deployment D]` lists every Integration document with the same local
  readiness (reads each local tool's version; never contacts a remote service). A `type: git`
  document is checked as the `git` CLI that source sync, remotes, assets and audit pushes run (PATH,
  `spec.version`). Another `sources`-only document whose type has no integration class is shown as
  a credential holder, not as a problem. A disabled document nothing uses is listed, never warned
  about. With `--deployment` it adds what that deployment binds at deploy time - step
  provisioners, value stores, the output store, gate policies (including types bound without a
  document) - and exits 3 when one of those is not ready: an offline CI preflight.
