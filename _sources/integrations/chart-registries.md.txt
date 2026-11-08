# Chart registries (`capabilities: [sources]`)

Lets strata pull a Helm chart from a registry that needs a login: an HTTP chart repository
(a ProGet Helm feed, Nexus, Artifactory, ChartMuseum) or an OCI registry (a ProGet container feed, a
private GHCR or ACR repository). A public registry needs none of this; a module can name it inline
with `chart_repository` and no credentials.

The registry is declared once as a remote in `strata.yaml`, and the remote names an
[`integration`](../config/integration.md) that holds the authentication, exactly as a private git
remote does ([git.md](git.md)).

## Prerequisites

- A username and a password, token or API key with read access to the feed.
- That secret declared in an [`environment`](../config/environment.md) the deployment reaches.
  `build run` pulls the chart, and reads only `constant` and `environment` stores unless you pass
  `--resolve`, so a secret in Key Vault or another integration-backed store needs
  `build run --resolve`. Without it the chart is left unpinned and the deploy refuses it.
- `helm` on `PATH` wherever `build run` runs, as well as where `deploy` runs: Helm 4, or Helm 3 (deprecated) declared
  with `spec.version: "<4"` on a `helm` integration
  ([helm-chart-deployment.md](../design/helm-chart-deployment.md#helm-versions)).

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

## What build and deploy do

`build run` pulls each registry chart into `.strata/helm/` and pins it: the module's `meta.yaml`
records the exact version and the archive's SHA-256. The deploy installs that archive, so the registry
is contacted at deploy only when the cache is missing (a deploy job that does not share the build's
`.strata/`), and then only to pull the pinned version again and compare it
([helm-chart-deployment.md](../design/helm-chart-deployment.md)).

- Credentials are read only for the registries the modules actually pull charts from. A remote
  nothing pulls from is never looked at, so a mistake in it cannot fail a run. An exact
  `chart_version` already in the cache is not pulled at all.
- A local chart's `dependencies` are pulled the same way, into the built chart's `charts/`
  ([helm-chart-dependencies.md](../work/helm-chart-dependencies.md)). To give a private dependency
  repository credentials, declare a `helm` or `oci` remote with the same URL as the dependency's
  `repository` and an `integration`; any other dependency repository is pulled anonymously.
- For an HTTP repository: `helm repo add <alias> <url> --force-update --username <user> --password-stdin`.
  For an OCI registry: `helm registry login <host> --username <user> --password-stdin`.
- The password reaches Helm **only on stdin**. It is never on a command line, in an environment
  variable, in the output, or in an error message: a failed login's text has it replaced by `***`.
- Every pull gets its own temporary Helm configuration (repository list, index cache and registry
  login), deleted when the pull ends, also when Helm fails. Your own Helm configuration is never
  touched, so the password is not left behind in it.
- A rejected login is an error for that module.

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

`build run`, and a deploy that has to pull again, report two more, before any `helm` command for that pull:

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

Verified with Helm 3.13.2, and the same pulls again with Helm 4.3.1, against a local chart server and a registry endpoint that require HTTP Basic
authentication: the login and chart pull succeed with the credentials, fail without them, nothing is put
on the command line, and the user's own Helm configuration is byte-identical afterwards. It has **not**
been run against a real ProGet instance, and an authenticated OCI chart pull is untested. Confirm
ProGet's basic-auth convention for your version (`api` plus an API key, or a user and password); strata
only needs a literal username and a secret.
