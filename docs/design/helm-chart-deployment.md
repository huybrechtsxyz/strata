# Helm Chart Deployment — Design

- Status: current
- Last updated: 2026-10-21

## Overview

A `type: helm` module deploys either a **local chart** (shipped in the consumer's repository and copied into the build) or
a **registry chart** (pulled by Helm itself from an OCI registry or an HTTP chart repository). This doc describes how both
are built, checked and deployed, including a private registry that needs a login. The evidence behind it is haven: 25
forge modules, 19 local charts and 6 registry charts (3 OCI: cert-manager, homarr, immich; 3 HTTP: gatus, jellyfin,
nextcloud), every registry chart pinned with a `chart_version`.

## Current Design

### Sources

`SourceModel` names a chart one of two ways (see [module.md](../config/module.md)):

- **Local**: `source_path` (plus an optional git `remote`). `sync_module_source()` copies the chart verbatim into the
  module's build directory, with no templating ([ADR-0025](../decisions/0025-strata-supplies-input-not-source-rewriting.md)).
- **Registry**: `chart_name` plus `chart_version`, and either a named `remote` (`type: helm` or `oci`) or an inline
  `chart_repository`. Nothing is copied; `meta.yaml` carries `chartName`/`chartVersion`/`chartRemote|chartRepository` so a
  deploy needs nothing else. `pins.charts` is overlaid onto `chart_version` before rendering.

### Build (`build run`)

`build run` never executes Helm ([ADR-0022](../decisions/0022-strata-build-run.md)). For each module,
`prepare_namespace()` writes `values.yaml` (the module's `services` and `configuration`) and `meta.yaml` into the module
directory, overwriting the copied chart's own `values.yaml`.

For a local chart, `build_workload_modules()` first compares `module.spec.configuration` against the chart's own
`values.yaml` (read right after the copy, before it is overwritten): a key the chart does not declare is a
`helm_unknown_configuration_key` warning with a close-match suggestion. The check covers the top level plus one nested level,
and an empty default mapping (`env: {}`) is treated as free-form.

### Check phase (`deploy check`, `deploy plan`, `deploy run`)

`HelmIntegration.check_namespace()` runs `helm lint` on every local chart ([ADR-0043](../decisions/0043-helm-lint-as-the-container-check-phase.md)):

- The target is the built module directory, with a temporary `-f` overlay holding the module's resolved non-secret values.
  Secret-shaped leaves stay literal tokens, so no secret reaches disk or argv. A `${output:}` whose upstream step has not run
  yet gets a placeholder, because a check must not depend on infrastructure that does not exist yet. The overlay lives outside
  the build directory, which a check must not alter.
- Registry charts are skipped (`helm lint` cannot lint a remote chart).
- Only `[ERROR]` findings fail: one `helm_lint_failed` error per module, carrying lint's own message.
- The base `InfraIntegration.check_namespace()` raises `CheckPhaseUnsupportedError`, which `deploy_run()` reports as "no
  check phase" (Compose). Any other `IntegrationError` from a check is a `check_failed` error.

| Command                     | Helm step                                               |
| --------------------------- | ------------------------------------------------------- |
| `deploy setup`              | skipped (nothing to initialise)                         |
| `deploy check`              | lint, then stop                                         |
| `deploy plan` / `--dry-run` | lint, then `helm upgrade --dry-run`                     |
| `deploy run`                | lint, then `helm upgrade`                               |
| `deploy apply`              | `helm upgrade` only (applies what was already reviewed) |

### Deploy (`deploy_namespace()`)

One `helm upgrade --install` per module, from the built directory. `meta.yaml` supplies the release, namespace and chart
coordinates; `values.yaml`'s tokens are resolved and rewritten in place for non-secret leaves, and secret-shaped leaves go
through `--set-string`. `deploy_run()` makes `build_path` absolute first, because Helm runs with its working directory in the
module directory and a relative path would resolve twice.

Chart resolution (`_resolve_chart()`):

- **OCI** (`oci://` remote or inline repository): Helm resolves it natively, so the reference is `<url>/<chart>`.
- **HTTP**: `helm repo add <alias> <url> --force-update` registers the repository and refreshes its index in one call. A plain
  `repo add` skips an alias that already exists and leaves a stale index, which cannot resolve a newer pinned version. A
  failure is a `helm_repo_refresh_failed` warning, because a cached index may still satisfy the pin. The alias is a readable
  prefix plus 8 hex characters of the URL's hash (`twin-github-8ae8a4f3`): v1's 20-character truncation could give two
  registries one alias.
- **Version**: `meta.yaml`'s `chartVersion` is always passed as `--version`, to both the preview and the upgrade.

### Private registries

A registry that needs a login is declared once as a remote that names an integration
([ADR-0042](../decisions/0042-private-chart-registries-authenticate-through-the-remote-integration.md);
user guide: [chart-registries.md](../integrations/chart-registries.md)):

- The integration has `capabilities: [sources]` and `method: token` with a literal `username` and a key naming a secret.
  `method: cli` means "whatever Helm is already logged in with"; any other method is rejected.
- `deploy_run()` resolves credentials only for the helm/oci remotes the step's modules pull charts from
  (`_chart_registry_credentials()`), from the values it has already resolved, before any `helm` call for the step. Every
  problem is a `helm_registry_credentials_invalid` error naming the remote and integration.
- The password reaches Helm only on stdin (`Integration.run(input=...)`): `repo add ... --username U --password-stdin` for HTTP,
  `registry login <host> --username U --password-stdin` for OCI. It is never in argv, in the environment, or in output; a
  failed login's text has it replaced by `***` (`RegistryAuthError`, reported as `helm_registry_auth_failed` for that module
  only).
- Each credentialed module gets a temporary Helm configuration (`HELM_REPOSITORY_CONFIG`, `HELM_REPOSITORY_CACHE`,
  `HELM_REGISTRY_CONFIG`, `HELM_CACHE_HOME`) shared by its login, preview and upgrade, and removed when `deploy_namespace()`
  returns, including when Helm raises. The user's own Helm configuration is never touched. Inline `chart_repository` has no
  integration to name, so it is always anonymous.

### Validation

`strata validate` checks a `helm`/`oci` remote's integration with `_fetch_integration_problems(integration, remote_type)`. For a
chart remote every finding is an error, because nothing else uses a chart registry's integration, so each is a definite deploy
failure: `remote_integration_unknown`, `remote_integration_not_sources`, `remote_integration_disabled`,
`remote_integration_auth_unsupported` (anything but `token`/`cli`) and `remote_integration_username_required`. For a git remote
`disabled` and `auth_unsupported` stay warnings, because GitOps and audit pushes also use a git remote's integration.

### Diagnostics

| Code                                | Severity | Raised by                                                       |
| ----------------------------------- | -------- | --------------------------------------------------------------- |
| `helm_unknown_configuration_key`    | warning  | `build run`, a local chart's undeclared `configuration` key     |
| `helm_repo_refresh_failed`          | warning  | deploy, an HTTP repository that could not be added or refreshed |
| `helm_lint_failed`                  | error    | the check phase, `helm lint` reported an `[ERROR]`              |
| `check_failed`                      | error    | the check phase, an `IntegrationError` other than unsupported   |
| `helm_registry_credentials_invalid` | error    | deploy, credentials that cannot be built before any helm call   |
| `helm_registry_auth_failed`         | error    | deploy, the registry rejected the login                         |

### Code map

| Concern                       | Where                                                                                                                                    |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| Rendering, deploy, check      | `integrations/helm.py` (`prepare_namespace`, `deploy_namespace`, `check_namespace`)                                                      |
| Chart resolution, alias, auth | `integrations/helm.py` (`_resolve_chart`, `_chart_ref_from_url`, `_repo_alias`, `credentials_from_authentication`, `_isolated_helm_env`) |
| Check hook and error types    | `integrations/capabilities.py`, `integrations/errors.py`                                                                                 |
| stdin for secrets             | `integrations/base.py` (`Integration.run(input=)`)                                                                                       |
| Build-time key warning        | `controllers/workload_controller.py`                                                                                                     |
| Step dispatch, credentials    | `controllers/deploy_controller.py`                                                                                                       |
| Validation                    | `controllers/semantic_checks.py`                                                                                                         |

### Scope boundaries

Decided out of scope, not pending: managed-identity and token-exchange authentication (an ACR access token from `az acr
login`: log in yourself and use `method: cli`), TLS options and client certificates, `--pass-credentials` (so a feed that
redirects chart downloads to another host fails with a 401), credentials on an inline `chart_repository`, registry login for
Compose, and a validate-time check that the credential key is declared in every deployment's environment (the deploy-time error
names the key).

Test coverage: authentication was exercised with real `helm` against a local chart server and a registry endpoint that require
HTTP Basic auth. It has not been run against a real ProGet, an authenticated OCI chart pull is untested, and no live `deploy run`
has been done (no cluster).

## Related Decisions

- [ADR-0042](../decisions/0042-private-chart-registries-authenticate-through-the-remote-integration.md): credentials come from
  the remote's integration, on stdin, in an isolated Helm configuration.
- [ADR-0043](../decisions/0043-helm-lint-as-the-container-check-phase.md): `helm lint` of the built chart is the container check
  phase, and a lint error fails the deploy.
- [ADR-0022](../decisions/0022-strata-build-run.md): `build run` renders, never executes; why lint is not a build step.
- [ADR-0025](../decisions/0025-strata-supplies-input-not-source-rewriting.md): sources are copied verbatim, never templated.
- [deploy-pipeline-stage-commands.md](deploy-pipeline-stage-commands.md): the `setup`/`check`/`plan`/`apply` split the check phase
  slots into.

## History

- The starting point was a comparison against v1's source (`helm_deployer.py`, `helm_builder.py`) and haven's real modules and CI,
  not the documented behaviour. v1 had no registry authentication at all, so credentials are new capability, not parity.
- The largest gap was that `meta.yaml` carried `chartVersion` but deploy never passed `--version`, so `chart_version` and
  `pins.charts` were inert and every registry chart floated to the latest release. A test had encoded the omission as correct.
- `helm repo add` on an existing alias is skipped and does not refresh the index (probed with helm 3.13.2), so a pinned version
  newer than the cache fails with "chart not found". `--force-update` was chosen over a separate `helm repo update`: it is one
  stateless call, whereas a Helm step in `deploy setup` would reverse the rule that setup and check skip container steps and
  `deploy apply` would still not refresh.
- Helm rejects `repo add` with an existing alias and a different URL, and v1 ignored that failure, so a collision silently resolved
  a chart from the wrong repository. With `--force-update` it would have overwritten the first registration instead, which is why
  the alias became a hash.
- The key check is a port of v1's `_validate_helm_values`. v1's algorithm gave zero findings on haven's 19 local charts. The
  `{}` free-form rule is a deliberate change, since v1 would flag every child of an empty default mapping.
- Linting the source charts (what haven's CI does) is not the same as linting what deploys. A configuration typo passed the
  source lint and failed the built chart with a nil pointer. Linting the built directory with a resolved overlay was chosen over
  linting it with tokens left verbatim, which needs no resolution code but would false-fail on a template that pipes a token
  through `int`; that simpler form is the fallback if resolution proves fragile.
- `helm lint` exits non-zero only on an `[ERROR]` finding, and every haven chart reports "icon is recommended", so warnings and
  info are ignored. A registry chart cannot be linted at all.
- `helm repo add --password-stdin` stores the password in plain text in `repositories.yaml`, and credentials only work in the
  configuration they were added to. That is why Helm's state is isolated per credentialed module, and why `helm repo remove` or
  `helm registry logout` afterwards was rejected: a crash would leave the secret, and it would still edit the user's configuration.
- The chart download authenticates against the same host as the repository, so `--pass-credentials` is not needed and the safer
  default of not sending credentials to other domains stands.
- Deploy resolves every declared value before it runs a step, so registry credentials need no store access of their own. The
  git resolver (`RemoteCredentialResolver`) exists to read a few keys during a plain build without network, which does not apply
  to a deploy-time pull.
- Code review found that a relative `--build-path` broke Helm (a pre-existing defect, exposed by the new lint phase): the CLI does
  not resolve it, and `cwd=path.parent` doubled the path. It also led to signalling "no check phase" with a dedicated exception
  subclass rather than any `IntegrationError`, which would have hidden a real failure as a skip.
- To run `build run` against a real consumer checkout from a local fixture, a remote url cannot contain `..`, so link the checkout
  with a directory junction and add `discovery.exclude: [vendor]` so its own YAML and chart templates are not scanned as documents.
