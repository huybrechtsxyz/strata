# Helm Chart Deployment — Design

- Status: current
- Last updated: 2026-11-07

## Overview

A `type: helm` module deploys either a **local chart** (shipped in the consumer's repository and copied into the build) or
a **registry chart** (pulled from an OCI registry or an HTTP chart repository by `build run`, pinned, and installed from that
archive). This doc describes how both
are built, checked and deployed, including a private registry that needs a login. The evidence behind it is haven: 25
forge modules, 19 local charts and 6 registry charts (3 OCI: cert-manager, homarr, immich; 3 HTTP: gatus, jellyfin,
nextcloud), every registry chart pinned with a `chart_version`.

## Current Design

### Sources

`SourceModel` names a chart one of two ways (see [module.md](../config/module.md)):

- **Local**: `source_path` (plus an optional git `remote`). `sync_module_source()` copies the chart verbatim into the
  module's build directory, with no templating ([ADR-0025](../decisions/0025-strata-supplies-input-not-source-rewriting.md)).
- **Registry**: `chart_name` plus `chart_version` (an exact version or a range), and either a named `remote` (`type: helm`
  or `oci`) or an inline `chart_repository`. `build run` pulls it into `.strata/helm/` and pins it (below); `meta.yaml`
  carries `chartName`/`chartVersion`/`chartRemote|chartRepository` and, once pinned, `chartDigest`. `pins.charts` is
  overlaid onto `chart_version` before the pull.

### Build (`build run`)

`build run` never installs or renders a release ([ADR-0022](../decisions/0022-strata-build-run.md)); it only runs Helm to
pull charts and to list a local chart's dependencies
([ADR-0054](../decisions/0054-build-fetches-release-inputs-never-targets.md)). For each module,
`prepare_namespace()` writes `strata-values.yaml` (the module's `services` and `configuration`, `{}` when it sets nothing)
and `meta.yaml` into the module directory. A local chart's own `values.yaml` is left as copied: Helm reads it as the chart's
defaults, and `strata-values.yaml` overlays them, exactly as for a registry chart. The file is always written, so a deploy can
always pass it with `-f`.

`build_workload_modules()` also compares `module.spec.configuration` against the chart's own `values.yaml` (a local chart's,
or a pinned registry chart's in the cache): a key the chart does not declare is a `helm_unknown_configuration_key` warning
with a close-match suggestion. The check covers the top level plus one nested level, and an empty default mapping
(`env: {}`) is treated as free-form. A key named after a dependency (its `alias` if it has one) or `global` is passed on
to subcharts, so neither it nor its children are checked.

**Registry charts are pinned** ([ADR-0054](../decisions/0054-build-fetches-release-inputs-never-targets.md);
[helm-registry-chart-checks.md](../work/helm-registry-chart-checks.md)). The build fetches the release's inputs, never
its targets, so a registry chart is pulled at build and installed from that exact archive at deploy:

- **Cache.** `.strata/helm/<source>/<chart>/<version>/` holds the archive and the archive unpacked (`helm_cache.py`).
  `<source>` is the remote name, or the repository's alias for an inline `chart_repository`; `<version>` is the exact
  version. The digest is strata's own SHA-256 of the archive, so a cache hit needs no `helm`. A pull lands in a
  temporary directory in the cache and is renamed into place.
- **Pin** (`_pin_registry_chart()`): an exact `chart_version` already cached is pinned from the cache; anything else is
  pulled (`HelmIntegration.pull_registry_chart()`: `_resolve_chart()` then `helm pull`, always in an isolated Helm
  configuration). A range is pulled on every build. `meta.yaml` then records the exact `chartVersion`,
  `chartVersionConstraint` (the declared range, when it differs), `chartDigest`, `chartAppVersion` and `chartSubcharts`
  (the charts in its `charts/`, at exact versions), so the SBOM reads the build output alone.
- **Unpinned.** A pull the machine cannot attempt (no `helm`, a registry credential that needs `--resolve`, or
  `build run --offline`) leaves the chart unpinned: a `helm_chart_not_pinned` warning, and `meta.yaml` keeps the
  declaration. The build succeeds; the deploy refuses the chart. A pull that fails is an error (`helm_chart_pull_failed`,
  `helm_registry_auth_failed`), and a cached version whose archive the registry has since changed is
  `helm_chart_digest_changed`.
- `build run --dry-run` reports `would pull …` or `would use cached …` and fetches nothing.

**A local chart's dependencies are placed in its `charts/`**
([helm-chart-dependencies.md](../work/helm-chart-dependencies.md)). `sync_module_source()` copies the chart alone, and
Helm installs only what is in `charts/`, so `_place_chart_dependencies()` fills the build copy itself. It never runs
`helm dependency build`, which writes a `Chart.lock` when there is none and cannot follow `file://` once the chart is
copied. The plan comes from the source (`helm_dependencies.plan_dependencies()`), never the build copy:

- **Committed** under the source's `charts/`: left as it is.
- **Locked** in a committed `Chart.lock` (or `requirements.lock`): the lock's version. A dependency the lock lacks as
  declared is `helm_chart_dependency_lock_out_of_sync`.
- **Exact or range**, no lock: pulled through the chart cache like a registry chart (`_pin_chart()`), a range resolved
  on every build. A repository URL that matches a declared helm or oci remote's URL authenticates through that remote;
  any other is pulled anonymously.
- **`file://`**: the directory is copied unpacked to `charts/<name>/`, resolved against the chart's origin and refused
  outside its repository (`helm_chart_dependency_outside_source`); its own dependencies are placed the same way.
- **No repository** and not committed is `helm_chart_dependency_missing`; a Helm repository name (`@name`,
  `alias:name`) is `helm_chart_dependency_repository_name`.

Then, when `helm` is installed, `helm dependency list` checks the result offline: anything but `ok` or `unpacked` is an
error (`helm_chart_dependency_missing`, `helm_chart_dependency_wrong_version`). `meta.yaml` records `chartDependencies`
(name, exact version, the declared constraint when it differs, repository, digest for an archive, origin `pulled`,
`cache`, `committed` or `file`). The unpinned rule applies as for registry charts: a dependency the machine cannot pull
is a `helm_chart_dependency_not_pinned` warning and is listed in `chartDependenciesUnpinned`. `--dry-run` does not
materialise the chart and so places no dependencies.

### Check phase (`deploy check`, `deploy plan`, `deploy run`)

Before the first step of `deploy run`, `plan` and `apply`, `_verify_registry_charts()` makes sure every registry chart of
the Helm steps to run is the one the build pinned: no `chartDigest` is `helm_chart_not_pinned`; the cache entry is used
when its digest matches; otherwise the chart is pulled again at exactly `chartVersion`, with the deploy's registry
credentials, and compared (`helm_chart_digest_changed` on a mismatch). A local chart whose `meta.yaml` lists
`chartDependenciesUnpinned` is `helm_chart_not_pinned` too; its pinned dependencies are already in the build's
`charts/`, which the deploy trusts with the rest of the build output. Any problem stops the run before any step.
`--smoke-test` only reports that it would verify. The verified archives go to the check and the deploy as
`chart_archives`.

`HelmIntegration.check_namespace()` runs `helm lint` on every chart ([ADR-0043](../decisions/0043-helm-lint-as-the-container-check-phase.md)):

- The target is the built module directory (a registry chart: its verified chart in the cache), with a temporary `-f`
  overlay holding the module's resolved non-secret values.
  Secret-shaped leaves stay literal tokens, so no secret reaches disk or argv. A `${output:}` whose upstream step has not run
  yet gets a placeholder, because a check must not depend on infrastructure that does not exist yet. The overlay lives outside
  the build directory, which a check must not alter.
- A registry chart without a verified archive is skipped (only possible when `check_namespace()` is called directly).
- Only `[ERROR]` findings fail: one `helm_lint_failed` error per module, carrying lint's own message.
- The base `InfraIntegration.check_namespace()` raises `CheckPhaseUnsupportedError`, which `deploy_run()` reports as "no
  check phase" (Compose). Any other `IntegrationError` from a check is a `check_failed` error.

| Command                     | Helm step                                               |
| --------------------------- | ------------------------------------------------------- |
| `deploy setup`              | skipped (nothing to initialise)                         |
| `deploy check`              | lint, then stop                                         |
| `deploy plan` / `--dry-run` | lint, then `helm upgrade --dry-run=client`              |
| `deploy run`                | lint, then `helm upgrade`                               |
| `deploy apply`              | `helm upgrade` only (applies what was already reviewed) |

### Deploy (`deploy_namespace()`)

One `helm upgrade --install` per module, from the built directory, or for a registry chart from its verified archive
(`.tgz`), so no repository or registry is contacted at install time and no `--version` is passed. A registry chart
without a verified archive is a `helm_chart_not_pinned` error for that module. `meta.yaml` supplies the release and
namespace; `strata-values.yaml`'s tokens are resolved and rewritten in place for non-secret leaves. Secret-shaped leaves
stay tokens in the file and reach Helm as a second values document on stdin (`-f strata-values.yaml -f -`), so a secret is
never on disk or in argv. A module directory without `meta.yaml` or `strata-values.yaml` (a build from an older strata) is an
error for that module, not a deploy without values.

Each `helm upgrade` waits and rolls a failed release back: `--wait=watcher --rollback-on-failure` on Helm 4,
`--wait --atomic` on a declared Helm 3 (see [Helm versions](#helm-versions)). The stage's `timeouts.apply` (or `timeouts.plan` for a dry-run) applies to
each release: N seconds is the process timeout and N/2 Helm's own `--timeout`, so a rollback can finish before strata stops
Helm. The defaults are 600 s and `5m` ([cli-timeout.md](cli-timeout.md)). That document holds only the branches that lead to a secret, except that a list holding one is sent whole,
because Helm replaces lists instead of merging them. `deploy_run()` makes `build_path` absolute first, because Helm runs with
its working directory in the module directory and a relative path would resolve twice.

The `--dry-run` preview (`helm upgrade --dry-run=client`, or `--dry-run` on a declared Helm 3) is never streamed: its output is the rendered manifest, and a Secret's
`data` is base64, which the output redaction cannot mask. The step reports one `Kind/name` line per resource instead, hooks
marked, as the destroy preview does ([deploy-destroy.md](deploy-destroy.md)). The chart's `NOTES` are not shown either,
because they are rendered from the same values.

**The cluster.** Helm uses the kubeconfig the caller provides
([ADR-0020](../decisions/0020-v1-consumer-feature-priority.md)). A deployment with `spec.kube_context` pins every `helm`
call of its Helm steps to that context by adding `HELM_KUBECONTEXT` to the step's environment
(`HelmIntegration.target_context_env()`), and `deploy_run()` checks before the first step, with
`target_context_problem()` (`kube_context_problem()`), that the kubeconfig defines it and that the caller's environment
does not point Helm elsewhere (`HELM_KUBECONTEXT`, `HELM_KUBEAPISERVER`). Only the context names are read from the
kubeconfig; the cluster is not contacted ([helm-kube-context.md](../work/helm-kube-context.md)). Compose is pinned the
same way through `docker_context` ([compose-docker-context.md](../work/compose-docker-context.md)).

Chart resolution for a pull (`_resolve_chart()`, at build and when the deploy pulls a chart missing from its cache):

- **OCI** (`oci://` remote or inline repository): Helm resolves it natively, so the reference is `<url>/<chart>`.
- **HTTP**: `helm repo add <alias> <url> --force-update` registers the repository and refreshes its index in one call. A plain
  `repo add` skips an alias that already exists and leaves a stale index, which cannot resolve a newer pinned version. A
  failure is a `helm_repo_refresh_failed` warning, because a cached index may still satisfy the pin. The alias is a readable
  prefix plus 8 hex characters of the URL's hash (`twin-github-8ae8a4f3`): v1's 20-character truncation could give two
  registries one alias.
- **Version**: the declared `chart_version` (exact or a range) at build; the pinned `chartVersion` at deploy.

### Helm versions

Strata passes Helm 4's flags by default. Helm 3 is deprecated, and gets its own flags only when the solution declares it
([ADR-0055](../decisions/0055-helm-4-by-default-helm-3-only-when-declared.md),
[helm-4-support.md](../work/helm-4-support.md)):

| Purpose                     | Helm 4 (the default)    | Helm 3 (declared)  |
| --------------------------- | ----------------------- | ------------------ |
| Preview (`plan()`)          | `--dry-run=client`      | `--dry-run`        |
| Deploy, wait                | `--wait=watcher`        | `--wait`           |
| Deploy, roll back a failure | `--rollback-on-failure` | `--atomic`         |

The configuration chooses, never the binary on `PATH`: `HelmIntegration.helm_major_declared()` returns 3 when the
`spec.version` of the Helm integration the step is bound to allows no Helm 4 (it matches neither `4.0.0` nor
`4.999.999`: `"<4"`, `"~=3.16"`, `"==3.13.2"`), else 4. To stay on Helm 3:

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: integration
meta:
  name: helm
spec:
  type: helm
  capabilities: [container]
  version: "<4"   # Helm 3 flags; deprecated
```

- `--wait=watcher` watches resources with kstatus, so the deploy identity needs the `watch` verb on the resources the
  chart creates (Helm 3's wait needed only `get` and `list`). It is named explicitly because `--rollback-on-failure`
  would otherwise choose a wait mode itself.
- Server-side apply is left at Helm 4's default (`--server-side auto`). A release upgraded by Helm 3 after Helm 4 is
  untested; declaring the major keeps one solution on one Helm.
- Helm sets `READ_VERSION`, so every readiness check runs `helm version` (no cluster contact) before anything applies.
  Helm 3 without the declaration is `helm_3_not_declared`, an error naming both fixes, except under `deploy destroy`:
  `helm uninstall` passes none of these flags, so there it is a warning and the step runs. Declared Helm 3 is a
  `helm_3_deprecated` warning, without a date. Declared Helm 3 with Helm 4 installed is the existing
  `integration_version_mismatch`.
- The deploy output names the version and path once per tool (`helm 4.3.1 (/usr/bin/helm)`), and the deployment
  manifest records them per provisioner (`artifacts.providers[].tool_version`/`tool_path`).

### Private registries

A registry that needs a login is declared once as a remote that names an integration
([ADR-0042](../decisions/0042-private-chart-registries-authenticate-through-the-remote-integration.md);
user guide: [chart-registries.md](../integrations/chart-registries.md)):

- The integration has `capabilities: [sources]` and `method: token` with a literal `username` and a key naming a secret.
  `method: cli` means "whatever Helm is already logged in with"; any other method is rejected.
- `build run` reads them through `RemoteCredentialResolver.for_registry()`, under the build's store rule: a key in a store a
  plain build does not read leaves the chart unpinned (`CredentialsNeedResolveError`, use `--resolve`); any other problem
  is a `helm_registry_credentials_invalid` error.
- `deploy_run()` needs them only to pull a pinned chart missing from its cache. It resolves credentials only for the
  helm/oci remotes those modules pull from (`_chart_registry_credentials()`), from the values it has already resolved,
  before any `helm` call. Every problem is a `helm_registry_credentials_invalid` error naming the remote and integration.
- The password reaches Helm only on stdin (`Integration.run(input=...)`): `repo add ... --username U --password-stdin` for HTTP,
  `registry login <host> --username U --password-stdin` for OCI. It is never in argv, in the environment, or in output; a
  failed login's text has it replaced by `***` (`RegistryAuthError`, reported as `helm_registry_auth_failed` for that module
  only).
- Every pull, credentialed or not, gets a temporary Helm configuration (`HELM_REPOSITORY_CONFIG`, `HELM_REPOSITORY_CACHE`,
  `HELM_REGISTRY_CONFIG`, `HELM_CACHE_HOME`) shared by its login and `helm pull`, and removed when
  `pull_registry_chart()` returns, including when Helm raises. The user's own Helm configuration is never touched. Inline
  `chart_repository` has no integration to name, so it is always anonymous.

### Validation

`strata validate` checks a `helm`/`oci` remote's integration with `_fetch_integration_problems(integration, remote_type)`. For a
chart remote every finding is an error, because nothing else uses a chart registry's integration, so each is a definite deploy
failure: `remote_integration_unknown`, `remote_integration_not_sources`, `remote_integration_disabled`,
`remote_integration_auth_unsupported` (anything but `token`/`cli`) and `remote_integration_username_required`. For a git remote
`disabled` and `auth_unsupported` stay warnings, because GitOps and audit pushes also use a git remote's integration.

It also checks the names each enabled Helm module reference deploys as (`release_identity()`, the function `meta.yaml` is written
with), defaults included, because a default is a strata name and may contain `_` ([helm-release-names.md](../work/helm-release-names.md)):

- `_check_helm_release_names()`, over every Namespace document: the release name must match Helm's own rule (verified against
  `helm template`, at most 53 characters) and the Kubernetes namespace must be a DNS-1123 label (at most 63 characters). A
  name set in a module field is reported once on the module; a default once on the namespace.
- `_check_workspace_helm_releases()`, per workspace: no two references install the same release into the same Kubernetes
  namespace. A pair with an invalid name is left to the first check.

Both are errors, with no opt-out: Helm or the API server would reject the same names at deploy.

`_check_deployment_target_contexts()` warns on a deployable deployment that runs a Helm step (a provisioner whose tool, or
named integration, is `helm`) and has no `kube_context`. The deployment schema rejects a `${...}` token in the field.

### Diagnostics

| Code                                | Severity | Raised by                                                       |
| ----------------------------------- | -------- | --------------------------------------------------------------- |
| `helm_unknown_configuration_key`    | warning  | `build run`, a configuration key the chart does not declare     |
| `helm_chart_not_pinned`             | warning  | `build run`, a registry chart the machine could not pull        |
| `helm_chart_not_pinned`             | error    | deploy, before any step, a registry chart or a local chart's dependency the build did not pin |
| `helm_chart_dependency_not_pinned`  | warning  | `build run`, a local chart's dependency the machine could not pull |
| `helm_chart_dependency_lock_out_of_sync` | error | `build run`, a committed `Chart.lock` lacks a declared dependency |
| `helm_chart_dependency_missing`     | error    | `build run`, a dependency with no repository that is not committed, or `missing` in `helm dependency list` |
| `helm_chart_dependency_wrong_version` | error  | `build run`, `helm dependency list` reports another status      |
| `helm_chart_dependency_repository_name` | error | `build run`, a dependency names a Helm repository instead of a URL |
| `helm_chart_dependency_outside_source` | error | `build run`, a `file://` dependency outside the chart's repository |
| `helm_chart_pull_failed`            | error    | build or deploy, `helm pull` failed                             |
| `helm_chart_digest_changed`         | error    | build or deploy, an archive differs from the pinned digest      |
| `helm_repo_refresh_failed`          | warning  | a pull, an HTTP repository that could not be added or refreshed |
| `helm_lint_failed`                  | error    | the check phase, `helm lint` reported an `[ERROR]`              |
| `check_failed`                      | error    | the check phase, an `IntegrationError` other than unsupported   |
| `helm_registry_credentials_invalid` | error    | build or deploy, credentials that cannot be built               |
| `helm_registry_auth_failed`         | error    | build or deploy, the registry rejected the login                |
| `helm_release_name_invalid`         | error    | validate, a release name Helm would reject                      |
| `helm_kubernetes_namespace_invalid` | error    | validate, a Kubernetes namespace the API server would reject    |
| `helm_release_conflict`             | error    | validate, two references install one release in one namespace   |
| `helm_kube_context_unset`           | warning  | validate, a deployment with a Helm step names no kube context    |
| `helm_kube_context_missing`         | error    | deploy, before any step, the kubeconfig has no such context      |
| `helm_kube_context_conflict`        | error    | deploy, before any step, the environment points Helm elsewhere   |
| `helm_3_not_declared`               | error    | deploy, before any step, Helm 3 installed but not declared (a warning under `deploy destroy`; `build run` and `strata integration` report it for a declared `helm` document) |
| `helm_3_deprecated`                 | warning  | the readiness check, Helm 3 installed and declared               |

### Code map

| Concern                       | Where                                                                                                                                    |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| Rendering, deploy, check      | `integrations/helm.py` (`prepare_namespace`, `deploy_namespace`, `check_namespace`)                                                      |
| Helm 3 or 4 flags, version rules | `integrations/helm.py` (`helm_major_declared`, `version_problems`, `version_notices`), `controllers/integration_readiness.py`        |
| Pull, chart resolution, auth  | `integrations/helm.py` (`pull_registry_chart`, `_resolve_chart`, `_chart_ref_from_url`, `_repo_alias`, `credentials_from_authentication`, `_isolated_helm_env`) |
| Chart cache, digest           | `integrations/helm_cache.py`                                                                                                             |
| Build-time pin                | `controllers/workload_controller.py` (`_pin_registry_chart`, `_pin_chart`), `controllers/remote_credentials.py` (`for_registry`)       |
| Local chart dependencies      | `integrations/helm_dependencies.py` (plan, record), `controllers/workload_controller.py` (`_place_chart_dependencies`, `_check_dependency_list`) |
| Deploy-time verification      | `controllers/deploy_controller.py` (`_verify_registry_charts`)                                                                           |
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
- [ADR-0054](../decisions/0054-build-fetches-release-inputs-never-targets.md): `build run` may fetch what a release deploys,
  never where it deploys; an input the machine cannot fetch is left unpinned and the deploy refuses it.
- [ADR-0055](../decisions/0055-helm-4-by-default-helm-3-only-when-declared.md): Helm 4's flags by default; Helm 3 only when
  the Helm integration's `spec.version` declares it, checked before anything applies.
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
- 2026-11-07: registry charts moved from a deploy-time `helm upgrade <ref> --version` to a build-time pull
  ([helm-registry-chart-checks.md](../work/helm-registry-chart-checks.md), gap 8 of the Helm gap analysis). Before, a
  registry chart got neither the key check nor lint, the SBOM recorded the declared version (possibly a range), and an
  unreachable registry failed the deploy mid-way. A check-phase pull was designed first and dropped: it came after the SBOM
  was written. Earlier reasons above that mention a deploy-time pull describe that previous design.
- 2026-11-08: a local chart's dependencies are placed at build
  ([helm-chart-dependencies.md](../work/helm-chart-dependencies.md), gap 9). Before, a chart that did not commit its
  dependencies under `charts/` passed lint (Helm only warns) and failed at `helm upgrade`, and a `file://` dependency
  could never work from the build copy. `helm dependency build` was rejected: about 18 OMP charts declare dependencies
  and only 2 commit a `Chart.lock`, so it would mostly have written a lock of its own.
- To run `build run` against a real consumer checkout from a local fixture, a remote url cannot contain `..`, so link the checkout
  with a directory junction and add `discovery.exclude: [vendor]` so its own YAML and chart templates are not scanned as documents.
- Helm 4 (checked with 4.3.1 on 2026-11-08) deprecates `--atomic` and a bare `--dry-run`, and a bare `--wait` means
  `watcher` there; Helm 3 rejects every replacement (`--wait=watcher` is "invalid argument"). Detecting the installed
  major at run time was rejected because one configuration would then deploy with different flags on different
  agents; the Helm integration's `spec.version` chooses instead, and the preflight stops undeclared Helm 3 before an
  earlier step applies ([ADR-0055](../decisions/0055-helm-4-by-default-helm-3-only-when-declared.md)).
