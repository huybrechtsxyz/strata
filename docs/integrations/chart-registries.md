# Chart registries (`capabilities: [sources]`)

Lets `strata deploy` pull a Helm chart from a registry that needs a login: an HTTP chart repository
(a ProGet Helm feed, Nexus, Artifactory, ChartMuseum) or an OCI registry (a ProGet container feed, a
private GHCR or ACR repository). A public registry needs none of this; a module can name it inline
with `chart_repository` and no credentials.

The registry is declared once as a remote in `strata.yaml`, and the remote names an
[`integration`](../config/integration.md) that holds the authentication, exactly as a private git
remote does ([git.md](git.md)).

## Prerequisites

- A username and a password, token or API key with read access to the feed.
- That secret declared in an [`environment`](../config/environment.md) the deployment reaches.
  `deploy` resolves every declared value before it runs a step, so any store works: an environment
  variable, a constant, or an integration-backed store such as Key Vault.
- `helm` 3.x on `PATH` (the same one `deploy` already needs).

## Configuring the `kind: integration` document

`spec.authentication` names a **key** of a secret; the value itself never appears in a document. Only
`method: token` can authenticate a chart registry, and `username` is **required** (it is a literal,
not a key). `spec.type` is an open string: `proget` below is a label, not a class strata looks up.

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: integration
meta:
  name: proget
spec:
  type: proget
  capabilities: [sources]
  authentication:
    method: token
    token:
      token: PROGET_API_KEY   # the key of a secret
      username: api           # ProGet accepts "api" with an API key as the password
```

`method: cli` (or no `authentication`) means "whatever Helm is already logged in with", for example
after `helm registry login` on the machine. Any other method is an error.

The secret, in the environment:

```yaml
spec:
  secrets:
    - key: PROGET_API_KEY
      store: environment
      value: PROGET_PAT       # the job exports this variable from its CI secret
```

## Declaring the registry

```yaml
# strata.yaml
spec:
  remotes:
    # an HTTP chart repository
    - name: proget-charts
      type: helm
      url: https://proget.example.com/helm/charts
      integration: proget

    # an OCI registry: the url is the registry parent path, the chart name is appended
    - name: proget-oci
      type: oci
      url: oci://proget.example.com/feeds/charts
      integration: proget
```

No `reference` is needed on either: a chart's version is pinned by the module's `chart_version`.

## Using it in a module

```yaml
spec:
  type: helm
  source:
    remote: proget-charts
    chart_name: gatus
    chart_version: "1.0.0"
```

A private registry has to be a named `remote`. The inline `chart_repository` form has nowhere to name an
integration, so it is always anonymous.

## What deploy does

- Credentials are read only for the registries a step's modules actually pull charts from. A remote
  nothing deploys from is never looked at, so a mistake in it cannot fail a run.
- For an HTTP repository: `helm repo add <alias> <url> --force-update --username <user> --password-stdin`.
  For an OCI registry: `helm registry login <host> --username <user> --password-stdin`.
- The password reaches Helm **only on stdin**. It is never on a command line, in an environment
  variable, in the output, or in an error message: a failed login's text has it replaced by `***`.
- Each module that needs credentials gets its own temporary Helm configuration (repository list,
  index cache and registry login). The module's login, its `--dry-run` preview and its `helm upgrade`
  all share it, and it is deleted when the namespace has been deployed, or when Helm fails. Your own
  Helm configuration is never touched, so the password is not left behind in it.
- `deploy plan` and `deploy run --dry-run` authenticate the same way. `deploy check` only lints local
  charts, so it needs no secret, and `build run` never pulls a chart.
- A rejected login is an error for that module only; the others still deploy.

## Validation

`strata validate` checks, before any deploy. For a `helm` or `oci` remote these are all errors, because
nothing else uses a chart registry's integration, so each is a definite deploy failure:

| Code                                   | When                                                                        |
| -------------------------------------- | --------------------------------------------------------------------------- |
| `remote_integration_unknown`           | the remote names an integration that does not exist                         |
| `remote_integration_not_sources`       | the integration does not declare `sources`                                  |
| `remote_integration_disabled`          | the integration has `enabled: false`                                        |
| `remote_integration_auth_unsupported`  | the method is not `token` or `cli` (an SSH key cannot log in to a registry) |
| `remote_integration_username_required` | `method: token` without `username`                                          |

For a git remote the `disabled` and `auth_unsupported` findings are warnings instead, because GitOps and
audit pushes also use a git remote's integration ([git.md](git.md)).

`deploy` reports two more, each before it runs any `helm` command for the step:

| Code                                | Meaning                                                                                                                                                                           |
| ----------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `helm_registry_credentials_invalid` | the integration is missing or disabled, its method or username is unusable, or the secret has no value (for example the key is declared in no environment the deployment reaches) |
| `helm_registry_auth_failed`         | the registry rejected the login (usually a wrong username or secret); only that module is skipped                                                                                 |

## Not supported

- Managed identity and token exchange, such as an ACR access token from `az acr login`. Log in
  yourself and use `method: cli`, or put a token in a secret.
- TLS options (`--ca-file`, `--insecure-skip-tls-verify`) and client certificates.
- `--pass-credentials`. Credentials are sent only to the registry host, so a feed that redirects chart
  downloads to another host fails with a 401.
- An `integration` on the inline `chart_repository`, and registry logins for Compose.

## How far this has been tested

Verified with Helm 3.13.2 against a local chart server and a registry endpoint that require HTTP Basic
authentication: the login and chart pull succeed with the credentials, fail without them, nothing is put
on the command line, and the user's own Helm configuration is byte-identical afterwards. It has **not**
been run against a real ProGet instance, and an authenticated OCI chart pull is untested. Confirm
ProGet's basic-auth convention for your version (`api` plus an API key, or a user and password); strata
only needs a literal username and a secret.
